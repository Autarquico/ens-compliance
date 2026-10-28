import json
import logging
import traceback

import httpx
import pytest

from ens_evidence.connectors.github import GitHubConnector
from ens_evidence.connectors.github.client import GitHubClient, GitHubError
from ens_evidence.connectors.github.connector import TOKEN_ENV
from ens_evidence.evidence.importer import canonical_json

from .fake_api import FACTURAS, FIXTURES, NOW, ORG, REPO, TOKEN, message, repo_body

CANARY = "CANARY"
FORBIDDEN_IN_LOGS = (TOKEN, CANARY, "Bearer", "Authorization")


def artifact(result) -> str:
    return canonical_json(result).decode()


def log_text(caplog) -> str:
    return "\n".join(f"{r.name} {r.getMessage()}" for r in caplog.records)


def debug_logging(caplog) -> None:
    caplog.set_level(logging.DEBUG)
    for name in ("httpx", "httpcore", "httpcore.http11", "httpcore.connection"):
        logging.getLogger(name).setLevel(logging.DEBUG)


def test_los_fixtures_contienen_canarios_distintos_del_token():
    canaries = {
        word
        for path in FIXTURES.glob("*.json")
        for word in path.read_text().replace('"', " ").split()
        if CANARY in word
    }

    assert len(canaries) >= 12
    assert TOKEN not in "".join(path.read_text() for path in FIXTURES.glob("*.json"))


def test_ningun_canario_llega_al_artefacto_guardado(collect):
    text = artifact(collect())

    assert CANARY not in text
    assert TOKEN not in text


@pytest.mark.parametrize(
    "canary",
    [
        "bot-CANARY",
        "user-CANARY",
        "org-desc-CANARY",
        "billing-CANARY",
        "member-CANARY",
        "install-account-CANARY",
        "repo-desc-CANARY",
        "owner-CANARY",
        "main-CANARY",
        "collab-CANARY",
        "reader-CANARY",
        "reviewer-CANARY",
        "title-CANARY",
        "ci-CANARY",
        "key-CANARY",
        "key2-CANARY",
        "x-CANARY",
    ],
)
def test_cada_canario_de_las_respuestas_esta_ausente_del_artefacto(collect, canary):
    assert canary not in artifact(collect())


def test_los_logins_y_titulos_se_sustituyen_por_recuentos(section):
    deploy_keys = section("deploy_keys")["items"][0]
    collaborators = section("collaborators")["items"][0]

    assert set(deploy_keys) == {"repository", "status", "count", "writable_count", "keys"}
    assert set(deploy_keys["keys"][0]) == {"read_only", "created_at"}
    assert set(collaborators) == {"repository", "status", "outside_total", "by_permission"}


def test_campos_extra_de_las_respuestas_no_se_persisten(section):
    repository = section("repositories")["items"][0]

    assert set(repository) == {
        "name",
        "visibility",
        "archived",
        "default_branch",
        "admin",
        "security_and_analysis_visible",
        "secret_scanning",
    }
    assert set(section("organization")["summary"]) == {
        "two_factor_requirement",
        "members_without_two_factor",
    }
    assert set(section("app_installations")["items"][0]) == {
        "app_slug",
        "repository_selection",
        "permissions",
        "suspended",
    }


def test_con_logging_debug_no_aparece_token_ni_canario_ni_cabecera_en_ningun_logger(
    collect, caplog
):
    debug_logging(caplog)

    collect()

    assert any(record.name.startswith("httpx") for record in caplog.records)
    text = log_text(caplog)
    for forbidden in FORBIDDEN_IN_LOGS:
        assert forbidden not in text, forbidden
    assert TOKEN not in caplog.text


def test_el_cuerpo_de_un_error_nunca_se_guarda_ni_se_registra(api, collect, caplog):
    debug_logging(caplog)
    api.set(rf"{REPO}/keys", (500, message("boom-CANARY", errors=[{"detail": "detail-CANARY"}])))
    api.set(rf"{REPO}/collaborators", (502, "<html>proxy-CANARY</html>"))
    api.set(rf"{REPO}/environments", (403, message("denied-CANARY")))
    api.set(rf"{REPO}/actions/permissions", (404, message("missing-CANARY")))

    result = collect()

    sections = result.data["sections"]
    assert sections["deploy_keys"]["items"][0]["reason"] == "HTTP 500: error de la API"
    assert sections["collaborators"]["items"][0]["reason"] == "HTTP 502: error de la API"
    assert sections["environments"]["items"][0]["reason"] == "HTTP 403: permiso insuficiente"
    assert CANARY not in artifact(result)
    assert CANARY not in log_text(caplog)


def test_un_mensaje_de_error_que_repite_el_token_nunca_llega_al_artefacto(api, collect, caplog):
    debug_logging(caplog)
    api.set(rf"{REPO}/keys", (403, message(f"token {TOKEN} sin permiso")))
    api.set(rf"{REPO}/vulnerability-alerts", (404, message(f"Bearer {TOKEN}")))

    result = collect()

    assert TOKEN not in artifact(result)
    assert TOKEN not in log_text(caplog)


def test_un_valor_con_el_token_dentro_de_una_respuesta_valida_no_se_persiste(api, collect):
    api.set(
        f"/orgs/{ORG}/installations",
        {"installations": [{"app_slug": TOKEN, "permissions": {TOKEN: "read"}}]},
    )
    api.set(f"/orgs/{ORG}/repos", [repo_body(visibility=TOKEN)])

    result = collect()

    assert TOKEN not in artifact(result)


def test_un_fallo_de_red_cuyo_texto_incluye_el_token_solo_deja_el_tipo(config, caplog):
    debug_logging(caplog)

    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"no conecta con Bearer {TOKEN}")

    connector = GitHubConnector(
        transport=httpx.MockTransport(broken), clock=lambda: NOW, env={TOKEN_ENV: TOKEN}
    )

    result = connector.collect(config)

    assert result.data["error"].endswith("ConnectError")
    assert TOKEN not in artifact(result)
    assert TOKEN not in log_text(caplog)


def test_las_excepciones_del_cliente_no_contienen_el_token_ni_encadenan_la_causa():
    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"no conecta con Bearer {TOKEN}")

    with GitHubClient(TOKEN, transport=httpx.MockTransport(broken)) as client:
        with pytest.raises(GitHubError) as error:
            client.get("user")

    rendered = "".join(traceback.format_exception(error.value))
    assert TOKEN not in rendered
    assert TOKEN not in repr(error.value)
    assert TOKEN not in str(error.value)


def test_las_excepciones_de_respuestas_con_el_token_en_el_cuerpo_no_lo_incluyen():
    def failing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json=message(f"fallo con {TOKEN}"))

    with GitHubClient(TOKEN, transport=httpx.MockTransport(failing)) as client:
        with pytest.raises(GitHubError) as error:
            client.get("repos", ORG, "facturas")

    assert TOKEN not in "".join(traceback.format_exception(error.value))
    assert TOKEN not in repr(error.value)


def test_el_cliente_y_el_conector_no_exponen_el_token_en_repr_ni_en_el_resultado(
    collect, connector
):
    result = collect()

    with GitHubClient(TOKEN) as client:
        assert TOKEN not in repr(client)
    assert TOKEN not in repr(connector)
    assert TOKEN not in json.dumps(result.model_dump(mode="json"))


def test_una_redireccion_no_se_sigue_y_el_token_no_viaja_a_otro_destino(api, section):
    api.set(
        rf"{FACTURAS}/keys",
        (302, {}, {"Location": "https://evil.example/steal?x=1"}),
    )

    result = section("deploy_keys")

    entry = next(e for e in result["items"] if e["repository"] == "facturas")
    assert entry["status"] == "ERROR"
    assert entry["reason"] == "HTTP 302: redirección no seguida"
    assert {call.url.host for call in api.calls} == {"api.github.com"}
