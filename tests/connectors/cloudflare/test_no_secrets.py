import json
import logging

import httpx
import pytest

from ens_evidence.connectors.cloudflare import CloudflareConnector
from ens_evidence.connectors.cloudflare.connector import TOKEN_ENV
from ens_evidence.evidence.importer import canonical_json

from .fake_api import FIXTURES, NOW, TOKEN, ZONE, denied

CANARY = "CANARY"
FORBIDDEN_IN_LOGS = (TOKEN, CANARY, "Bearer", "Authorization")


def artifact(result) -> str:
    return canonical_json(result).decode()


def log_text(caplog) -> str:
    return "\n".join(f"{r.name} {r.getMessage()}" for r in caplog.records)


def test_los_fixtures_contienen_canarios_distintos_del_token():
    canaries = {
        word
        for path in FIXTURES.glob("*.json")
        for word in path.read_text().replace('"', " ").split()
        if CANARY in word
    }

    assert len(canaries) >= 9
    assert TOKEN not in "".join(path.read_text() for path in FIXTURES.glob("*.json"))


def test_ningun_canario_llega_al_artefacto_guardado(collect):
    result = collect()

    text = artifact(result)
    assert CANARY not in text
    assert TOKEN not in text


@pytest.mark.parametrize(
    "canary",
    [
        "txt-CANARY",
        "comment-CANARY",
        "svcpass-CANARY",
        "query-CANARY",
        "ana-CANARY",
        "domain-CANARY",
        "aud-CANARY",
        "origin-ip-CANARY",
        "zone-owner-CANARY",
        "expr-CANARY",
        "actor-CANARY",
    ],
)
def test_cada_canario_de_las_respuestas_esta_ausente_del_artefacto(collect, canary):
    assert canary not in artifact(collect())


def test_el_dns_solo_guarda_content_de_los_cname(collect):
    records = collect().data["sections"]["dns"]["items"][0]["records"]

    contents = {r["type"]: r.get("content") for r in records}
    assert "content" not in next(r for r in records if r["type"] == "TXT")
    assert "content" not in next(r for r in records if r["type"] == "A")
    assert contents["CNAME"].endswith(".cfargotunnel.com")
    assert all(set(r) <= {"type", "name", "proxied", "content"} for r in records)


def test_las_politicas_de_access_guardan_tipos_de_selector_nunca_valores(collect):
    policy = collect().data["sections"]["access_policies"]["items"][0]["policies"][0]

    assert policy["include"] == ["email", "email_domain"]
    assert policy["require"] == ["ip"]
    assert policy["exclude"] == []


def test_la_url_de_origen_se_reduce_a_esquema_host_y_puerto(collect):
    ingress = collect().data["sections"]["tunnels"]["items"][0]["ingress"]

    assert ingress[0]["service"] == "https://origin.internal:8443"


def test_campos_extra_de_las_respuestas_no_se_persisten(collect):
    sections = collect().data["sections"]

    assert set(sections["zones"]["items"][0]) == {"id", "name", "status", "paused", "type"}
    assert set(sections["access_applications"]["items"][0]) == {
        "id",
        "name",
        "type",
        "public_destinations",
    }
    assert set(sections["tunnels"]["items"][0]) == {
        "id",
        "name",
        "status",
        "config_src",
        "ingress",
        "inferred_complete",
        "ingress_status",
    }


def test_con_logging_debug_no_aparece_token_ni_canario_ni_cabecera_en_ningun_logger(
    collect, caplog
):
    caplog.set_level(logging.DEBUG)
    for name in ("httpx", "httpcore", "httpcore.http11", "httpcore.connection"):
        logging.getLogger(name).setLevel(logging.DEBUG)

    collect()

    assert any(record.name.startswith("httpx") for record in caplog.records)
    text = log_text(caplog)
    for forbidden in FORBIDDEN_IN_LOGS:
        assert forbidden not in text, forbidden
    assert TOKEN not in caplog.text


def test_el_cuerpo_de_un_error_nunca_se_guarda_ni_se_registra(api, collect, caplog):
    caplog.set_level(logging.DEBUG)
    api.set(
        f"/zones/{ZONE}/dns_records",
        (
            500,
            {
                "success": False,
                "errors": [{"code": 1001, "message": "boom", "error_chain": ["body-CANARY"]}],
                "messages": [{"message": "msg-CANARY"}],
                "result": {"leak": "result-CANARY"},
            },
        ),
    )
    api.set(f"/zones/{ZONE}/rulesets", (502, "<html>proxy-CANARY</html>"))

    result = collect()

    assert result.data["sections"]["dns"]["items"][0]["reason"] == "HTTP 500: 1001: boom"
    assert "mal formada" in result.data["sections"]["waf"]["items"][0]["reason"]
    assert CANARY not in artifact(result)
    assert CANARY not in log_text(caplog)


def test_un_mensaje_de_error_que_repite_el_token_sale_redactado(api, collect, caplog):
    caplog.set_level(logging.DEBUG)
    status, body = denied()
    body["errors"][0]["message"] = f"token {TOKEN} sin permiso"
    api.set(f"/zones/{ZONE}/dns_records", (status, body))

    result = collect()

    reason = result.data["sections"]["dns"]["items"][0]["reason"]
    assert "[redactado]" in reason
    assert TOKEN not in artifact(result)
    assert TOKEN not in log_text(caplog)


def test_un_mensaje_de_error_se_trunca_y_pierde_los_caracteres_de_control(api, collect):
    status, body = denied()
    body["errors"][0]["message"] = "a\x00b\n" + "x" * 1000
    api.set(f"/zones/{ZONE}/dns_records", (status, body))

    reason = collect().data["sections"]["dns"]["items"][0]["reason"]

    assert len(reason) <= len("HTTP 403: 10000: ") + 200
    assert "\x00" not in reason
    assert "\n" not in reason


def test_un_fallo_de_red_cuyo_texto_incluye_el_token_solo_deja_el_tipo(config, caplog):
    caplog.set_level(logging.DEBUG)

    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"no conecta con Bearer {TOKEN}")

    connector = CloudflareConnector(
        transport=httpx.MockTransport(broken), clock=lambda: NOW, env={TOKEN_ENV: TOKEN}
    )

    result = connector.collect(config)

    assert result.data["error"].endswith("ConnectError")
    assert TOKEN not in artifact(result)
    assert TOKEN not in log_text(caplog)


def test_el_conector_no_expone_el_token_en_repr_ni_en_el_resultado(collect, connector):
    result = collect()

    assert TOKEN not in repr(connector)
    assert TOKEN not in json.dumps(result.model_dump(mode="json"))
