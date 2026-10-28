from .fake_api import ACCOUNT, ZONE, envelope, fixture


def zone(name, ident):
    return {"id": ident, "name": name, "status": "active"}


def test_zona_del_inventario_que_el_token_no_ve_queda_en_missing_from_token(collect):
    result = collect(inventory_zones=["taller-norte.example", "ausente.example"])

    summary = result.data["sections"]["zones"]["summary"]
    assert summary["missing_from_token"] == ["ausente.example"]
    assert summary["not_in_inventory"] == []


def test_zona_visible_fuera_del_inventario_queda_en_not_in_inventory(api, collect):
    api.set(
        "/zones",
        envelope(
            [zone("taller-norte.example", ZONE), zone("extra.example", "zone-2")],
            {"total_pages": 1},
        ),
    )

    result = collect()

    summary = result.data["sections"]["zones"]["summary"]
    assert summary["not_in_inventory"] == ["extra.example"]
    assert summary["missing_from_token"] == []


def test_la_comparacion_de_zonas_ignora_mayusculas(collect):
    result = collect(inventory_zones=["Taller-Norte.EXAMPLE"])

    summary = result.data["sections"]["zones"]["summary"]
    assert summary == {"missing_from_token": [], "not_in_inventory": []}


def test_inventario_vacio_marca_todas_las_zonas_visibles_como_not_in_inventory(collect):
    result = collect(inventory_zones=[])

    summary = result.data["sections"]["zones"]["summary"]
    assert summary["not_in_inventory"] == ["taller-norte.example"]


def test_la_peticion_de_zonas_se_limita_a_la_cuenta(api, collect):
    collect()

    assert api.params("/zones")[0]["account.id"] == ACCOUNT
    assert fixture("zones")["result"][0]["id"] == ZONE
