import httpx
import pytest

from .fake_api import envelope


def zone(n):
    return {"id": f"z{n}", "name": f"z{n}.example", "status": "active"}


def zones_section(result):
    return result.data["sections"]["zones"]


def serve(pages, info):
    """Página `n` (1-based) -> `pages[n-1]` zonas; `info(n)` -> `result_info` de esa página."""

    def reply(request: httpx.Request) -> httpx.Response:
        number = int(request.url.params["page"])
        items = pages[number - 1] if number <= len(pages) else []
        return httpx.Response(200, json=envelope([zone(n) for n in items], info(number)))

    return reply


def test_total_count_con_el_per_page_efectivo_del_servidor_recorre_todas_las_paginas(api, collect):
    api.set("/zones", serve([[1, 2], [3, 4], [5]], lambda n: {"total_count": 5, "per_page": 2}))

    zones = zones_section(collect())

    assert zones["status"] == "SUCCESS"
    assert [z["id"] for z in zones["items"]] == ["z1", "z2", "z3", "z4", "z5"]
    assert [p["page"] for p in api.params("/zones")] == ["1", "2", "3"]


def test_total_count_sin_per_page_en_la_respuesta_usa_el_pedido(api, collect):
    api.set("/zones", serve([[1, 2, 3]], lambda n: {"total_count": 3}))

    zones = zones_section(collect())

    assert len(zones["items"]) == 3
    assert len(api.params("/zones")) == 1


def test_el_servidor_que_recorta_per_page_no_corta_la_paginacion_antes_de_tiempo(api, collect):
    api.set("/zones", serve([[1], [2], [3]], lambda n: {"total_count": 3, "per_page": 1}))

    assert len(zones_section(collect())["items"]) == 3


def test_pagina_vacia_antes_del_total_declarado_es_error(api, collect):
    api.set("/zones", serve([[1, 2], []], lambda n: {"total_count": 5, "per_page": 2}))

    zones = zones_section(collect())

    assert zones["status"] == "ERROR"
    assert "página vacía antes del total" in zones["reason"]
    assert zones["items"] == []


@pytest.mark.parametrize(
    "info",
    [
        pytest.param({"total_count": "cinco", "per_page": 2}, id="total-no-numerico"),
        pytest.param({"total_count": -1, "per_page": 2}, id="total-negativo"),
        pytest.param({"total_count": True, "per_page": 2}, id="total-booleano"),
        pytest.param({"total_count": 2.5, "per_page": 2}, id="total-decimal"),
        pytest.param({"total_count": 5, "per_page": 0}, id="per-page-cero"),
        pytest.param({"total_count": 5, "per_page": "dos"}, id="per-page-no-numerico"),
        pytest.param({"total_pages": -3}, id="total-pages-negativo"),
    ],
)
def test_total_invalido_es_error_de_seccion(api, collect, info):
    api.set("/zones", serve([[1, 2]], lambda n: info))

    zones = zones_section(collect())

    assert zones["status"] == "ERROR"
    assert zones["items"] == []


def test_sin_totales_sigue_hasta_una_pagina_vacia(api, collect):
    api.set("/zones", serve([[1, 2], [3]], lambda n: {}))

    zones = zones_section(collect())

    assert zones["status"] == "SUCCESS"
    assert [z["id"] for z in zones["items"]] == ["z1", "z2", "z3"]
    assert [p["page"] for p in api.params("/zones")] == ["1", "2", "3"]


def test_zones_pide_per_page_no_mayor_que_50_en_todas_las_paginas(api, collect):
    api.set("/zones", serve([[1], [2]], lambda n: {"total_pages": 2}))

    collect()

    assert {int(p["per_page"]) for p in api.params("/zones")} == {50}
