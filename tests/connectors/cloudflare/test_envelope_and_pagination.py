import httpx
import pytest

from ens_evidence.connectors.cloudflare.client import MAX_PAGES

from .fake_api import ACCOUNT, ZONE, envelope, fixture

RULESETS = f"/zones/{ZONE}/rulesets"
AUDIT = f"/accounts/{ACCOUNT}/logs/audit"


def section(result, name):
    return result.data["sections"][name]


def paged_zones(total_pages):
    def reply(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        body = envelope(
            [{"id": f"z{page}", "name": f"z{page}.example", "status": "active"}],
            {"page": page, "total_pages": total_pages},
        )
        return httpx.Response(200, json=body)

    return reply


def cursor_pages(param_path, cursors):
    """Una página por cursor devuelto; la última no trae cursor siguiente."""

    def reply(request: httpx.Request) -> httpx.Response:
        sent = request.url.params.get("cursor")
        index = 0 if sent is None else cursors.index(sent) + 1
        following = cursors[index] if index < len(cursors) else None
        info = {"cursors": {"after": following}} if param_path == "after" else {"cursor": following}
        result = [{"id": f"rs-{index}", "name": f"rs-{index}"}]
        return httpx.Response(200, json=envelope(result, info))

    return reply


@pytest.mark.parametrize(
    "body",
    [
        pytest.param((200, "<html>Bad gateway</html>"), id="no-json"),
        pytest.param((200, {"result": [], "success": True}), id="sin-errors"),
        pytest.param((200, {"errors": [], "success": True}), id="sin-result"),
        pytest.param((200, {"errors": [], "result": []}), id="sin-success"),
        pytest.param((200, {"errors": [], "result": [], "success": "true"}), id="success-no-bool"),
        pytest.param((200, [1, 2]), id="no-es-objeto"),
    ],
)
def test_envoltorio_mal_formado_deja_la_seccion_en_error(api, collect, body):
    api.set("/zones", body)

    zones = section(collect(), "zones")

    assert zones["status"] == "ERROR"
    assert "mal formada" in zones["reason"]
    assert zones["items"] == []


def test_respuesta_con_success_false_en_200_es_error_de_seccion(api, collect):
    api.set(f"/accounts/{ACCOUNT}/access/apps", (200, {**envelope([]), "success": False}))

    assert section(collect(), "access_applications")["status"] == "ERROR"


def test_resultado_que_no_es_lista_en_endpoint_de_listas_es_error(api, collect):
    api.set("/zones", envelope({"id": "z"}))

    zones = section(collect(), "zones")

    assert zones["status"] == "ERROR"
    assert "lista" in zones["reason"]


def test_elemento_con_forma_inesperada_es_error_de_seccion(api, collect):
    api.set("/zones", envelope([{"name": "sin-id.example"}], {"total_pages": 1}))

    zones = section(collect(), "zones")

    assert zones["status"] == "ERROR"
    assert zones["reason"] == "respuesta con forma inesperada"


def test_error_de_servidor_en_una_seccion_no_invalida_la_recogida(api, collect):
    api.set(RULESETS, (500, {"success": False, "errors": [{"code": 1, "message": "boom"}]}))

    result = collect()

    assert section(result, "waf")["status"] == "ERROR"
    assert section(result, "waf")["items"][0]["reason"].startswith("HTTP 500")
    assert result.collection_status.value == "SUCCESS"


def test_paginacion_por_paginas_recorre_todas(api, collect):
    api.set("/zones", paged_zones(3))

    zones = section(collect(), "zones")

    assert [z["id"] for z in zones["items"]] == ["z1", "z2", "z3"]
    assert [p["page"] for p in api.params("/zones")] == ["1", "2", "3"]


def test_pagina_fallida_deja_la_seccion_en_error_sin_datos_parciales(api, collect):
    def reply(request: httpx.Request) -> httpx.Response:
        if request.url.params["page"] == "2":
            return httpx.Response(500, json={"success": False, "errors": [], "result": None})
        return paged_zones(3)(request)

    api.set("/zones", reply)

    result = collect()

    assert section(result, "zones")["status"] == "ERROR"
    assert section(result, "zones")["items"] == []
    assert section(result, "dns")["status"] == "ERROR"


def test_total_pages_inconsistente_es_error(api, collect):
    api.set(
        "/zones",
        envelope([{"id": "z", "name": "z.example"}] * 100, {"page": 1, "total_pages": "dos"}),
    )

    assert section(collect(), "zones")["status"] == "ERROR"


def test_tope_de_paginas_deja_la_seccion_en_error(api, collect):
    api.set("/zones", paged_zones(10**6))

    zones = section(collect(), "zones")

    assert zones["status"] == "ERROR"
    assert "límite de páginas" in zones["reason"]
    assert len(api.params("/zones")) == MAX_PAGES


def test_rulesets_siguen_el_cursor_after_hasta_agotarlo(api, collect):
    api.set(RULESETS, cursor_pages("after", ["c1", "c2"]))

    waf = section(collect(), "waf")

    assert [r["id"] for r in waf["items"][0]["rulesets"]] == ["rs-0", "rs-1", "rs-2"]
    assert [p.get("cursor") for p in api.params(RULESETS)] == [None, "c1", "c2"]


def test_cursor_repetido_en_rulesets_es_error_de_la_zona(api, collect):
    def reply(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=envelope([{"id": "rs", "name": "n"}], {"cursors": {"after": "igual"}})
        )

    api.set(RULESETS, reply)

    waf = section(collect(), "waf")

    assert waf["status"] == "ERROR"
    assert "cursor de paginación repetido" in waf["items"][0]["reason"]
    assert "rulesets" not in waf["items"][0]
    assert len(api.params(RULESETS)) == 2


def test_audit_logs_siguen_el_cursor_y_cuentan_todas_las_paginas(api, collect):
    entry = fixture("audit_logs")["result"][0]

    def reply(request: httpx.Request) -> httpx.Response:
        sent = request.url.params.get("cursor")
        following = {None: "a", "a": "b", "b": None}[sent]
        return httpx.Response(200, json=envelope([entry, entry], {"cursor": following}))

    api.set(AUDIT, reply)

    audit = section(collect(), "audit_logs")

    assert audit["summary"]["count"] == 6
    assert audit["summary"]["complete"] is True
    assert [p.get("cursor") for p in api.params(AUDIT)] == [None, "a", "b"]


def test_cursor_repetido_en_audit_logs_es_error_y_no_completo(api, collect):
    entry = fixture("audit_logs")["result"][0]
    api.set(AUDIT, envelope([entry], {"cursor": "igual"}))

    audit = section(collect(), "audit_logs")

    assert audit["status"] == "ERROR"
    assert "cursor de paginación repetido" in audit["reason"]
    assert audit["summary"]["complete"] is False
    assert "count" not in audit["summary"]


def test_pagina_de_audit_logs_fallida_no_deja_recuento_parcial(api, collect):
    entry = fixture("audit_logs")["result"][0]

    def reply(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("cursor"):
            return httpx.Response(500, json={"success": False, "errors": [], "result": None})
        return httpx.Response(200, json=envelope([entry], {"cursor": "a"}))

    api.set(AUDIT, reply)

    audit = section(collect(), "audit_logs")

    assert audit["status"] == "ERROR"
    assert "count" not in audit["summary"]
