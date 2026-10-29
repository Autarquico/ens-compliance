import logging
import traceback

import httpx
import pytest

from ens_evidence.connectors.google_workspace import KEY_ENV, GoogleWorkspaceConnector
from ens_evidence.connectors.google_workspace.auth import CredentialRejected, ServiceAccountKey
from ens_evidence.evidence.importer import canonical_json

from .cli_support import run_collect
from .credentials import key_json, pem_canary, private_pem
from .fake_api import (
    ACCESS_TOKEN,
    ADMIN_EVENTS,
    FIXTURES,
    NOW,
    TOKEN_EVENTS,
    USERS,
    google_error,
)

CANARY = "CANARY"
BROKEN_PEM = (
    "-----BEGIN PRIVATE KEY-----\nmalformada-CANARY-0123456789\n-----END PRIVATE KEY-----\n"
)


def artifact(result) -> str:
    return canonical_json(result).decode()


AWS_LOGGERS = ("botocore", "boto3", "s3transfer", "urllib3", "moto")


def log_text(caplog, *, without_aws: bool = False) -> str:
    """Los logs de AWS (solo en la CLI con S3) llevan su propia cabecera Authorization."""
    return "\n".join(
        f"{r.name} {r.getMessage()}"
        for r in caplog.records
        if not (without_aws and r.name.startswith(AWS_LOGGERS))
    )


def secrets(api) -> list[str]:
    """Canarios de credenciales: clave privada, aserción JWT firmada y token de acceso."""
    return [pem_canary(), ACCESS_TOKEN, *api.assertions]


def assert_clean(text: str, api) -> None:
    for secret in secrets(api):
        assert secret not in text
    for forbidden in (CANARY, "Bearer", "Authorization", "PRIVATE KEY"):
        assert forbidden not in text, forbidden


def test_los_fixtures_contienen_canarios_en_campos_que_no_se_deben_persistir():
    canaries = {
        word
        for path in FIXTURES.glob("*.json")
        for word in path.read_text().replace('"', " ").split()
        if CANARY in word
    }

    assert len(canaries) >= 15
    assert ACCESS_TOKEN not in "".join(path.read_text() for path in FIXTURES.glob("*.json"))


def test_ningun_canario_ni_secreto_llega_al_artefacto_guardado(api, collect):
    result = collect()

    assert api.assertions
    assert_clean(artifact(result), api)


def test_las_respuestas_de_error_con_el_token_y_la_aserción_reflejados_no_llegan_al_artefacto(
    api, collect
):
    def reflect_bearer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403, json=google_error(403, "forbidden", request.headers["Authorization"])[1]
        )

    def reflect_assertion(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500, json={"error": "backend", "error_description": request.content.decode()}
        )

    api.set(USERS, reflect_bearer)
    api.set(TOKEN_EVENTS, google_error(502, "backendError", f"{ACCESS_TOKEN} body-CANARY"))
    api.set(ADMIN_EVENTS, (500, f"<html>{ACCESS_TOKEN} proxy-CANARY</html>"))

    result = collect()

    assert_clean(artifact(result), api)
    api.set("/token", reflect_assertion)
    failed = collect()
    assert_clean(artifact(failed), api)


def test_las_excepciones_de_clave_mal_formada_no_contienen_la_clave():
    key = ServiceAccountKey(key_json(private_key=BROKEN_PEM))

    with pytest.raises(CredentialRejected) as error:
        key.sign(b"mensaje")

    text = "".join(traceback.format_exception(error.value)) + repr(error.value)
    assert "malformada-CANARY" not in text
    assert pem_canary() not in text


def test_con_logging_debug_no_aparece_ningun_secreto_en_ningun_logger(api, collect, debug_logging):
    collect()

    assert any(record.name.startswith("httpx") for record in debug_logging.records)
    text = log_text(debug_logging)
    for secret in secrets(api):
        assert secret not in text
    for forbidden in (CANARY, "Bearer", "Authorization"):
        assert forbidden not in text, forbidden
    assert ACCESS_TOKEN not in debug_logging.text


def test_con_logging_debug_una_recogida_fallida_tampoco_registra_secretos(
    api, config, debug_logging
):
    api.set("/token", (400, {"error": "invalid_grant", "error_description": "desc-CANARY"}))
    connector = GoogleWorkspaceConnector(
        transport=api.transport, clock=lambda: NOW, env={KEY_ENV: key_json()}
    )

    result = connector.collect(config)

    text = log_text(debug_logging) + artifact(result)
    assert pem_canary() not in text
    assert CANARY not in text
    assert all(assertion not in text for assertion in api.assertions)


def test_con_logging_debug_una_clave_privada_invalida_no_registra_la_clave(
    api, config, debug_logging
):
    connector = GoogleWorkspaceConnector(
        transport=api.transport, clock=lambda: NOW, env={KEY_ENV: key_json(private_key=BROKEN_PEM)}
    )

    result = connector.collect(config)

    text = log_text(debug_logging) + artifact(result)
    assert "malformada-CANARY" not in text
    assert debug_logging.records == [] or all(
        r.levelno <= logging.DEBUG or "malformada" not in r.getMessage()
        for r in debug_logging.records
    )


def test_el_correo_de_los_actores_y_los_datos_de_recuperacion_no_salen_de_las_secciones_permitidas(
    collect,
):
    sections = collect().data["sections"]

    for name in ("admins", "oauth_token_events", "admin_events", "customer"):
        assert "@" not in str(sections[name])
    users = str(sections["users"])
    assert "recovery-CANARY" not in users
    assert "phone-CANARY" not in users


@pytest.fixture
def cli(api, workspace, install_connector, debug_logging):
    install_connector(transport=api.transport, clock=lambda: NOW, env={KEY_ENV: key_json()})
    return workspace


def test_la_salida_de_la_cli_y_los_logs_no_contienen_secretos_en_una_recogida_correcta(
    api, cli, debug_logging
):
    result = run_collect(cli)

    assert result.exit_code == 0, result.output
    assert_clean(result.output + log_text(debug_logging, without_aws=True), api)
    for secret in secrets(api):
        assert secret not in log_text(debug_logging)


def test_la_salida_de_la_cli_no_contiene_secretos_cuando_la_credencial_falla(
    api, cli, debug_logging
):
    api.set("/token", (400, {"error": "invalid_grant", "error_description": "desc-CANARY"}))

    result = run_collect(cli)

    assert result.exit_code == 1
    text = result.output + log_text(debug_logging)
    assert CANARY not in text
    assert pem_canary() not in text
    assert private_pem() not in text
