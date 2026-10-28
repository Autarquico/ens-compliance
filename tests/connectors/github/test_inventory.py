from .fake_api import ORG, repo_body


def summary(section) -> dict:
    return section("repositories")["summary"]


def test_repos_esperados_ausentes_del_token_se_listan(section):
    result = summary(lambda name: section(name, inventory_repos=["facturas", "infra", "legacy"]))

    assert result["missing_from_token"] == ["legacy"]
    assert result["not_in_inventory"] == []


def test_repos_del_token_fuera_del_inventario_se_listan(section):
    result = summary(lambda name: section(name, inventory_repos=["facturas"]))

    assert result["missing_from_token"] == []
    assert result["not_in_inventory"] == ["infra"]


def test_inventario_vacio_deja_todos_los_repos_fuera_de_inventario(section):
    result = summary(lambda name: section(name, inventory_repos=[]))

    assert result["not_in_inventory"] == ["facturas", "infra"]


def test_la_comparacion_no_distingue_mayusculas(section):
    result = summary(lambda name: section(name, inventory_repos=["Facturas", "INFRA"]))

    assert result == {"missing_from_token": [], "not_in_inventory": []}


def test_token_sin_acceso_a_ningun_repo_marca_todos_como_ausentes(api, section):
    api.set(f"/orgs/{ORG}/repos", [])

    result = summary(section)

    assert result["missing_from_token"] == ["facturas", "infra"]
    assert section("repositories")["status"] == "SUCCESS"


def test_repo_extra_del_token_se_recoge_igualmente(api, section):
    api.set(
        f"/orgs/{ORG}/repos", [repo_body("facturas"), repo_body("infra"), repo_body(name="otro")]
    )

    result = section("repositories")

    assert result["summary"]["not_in_inventory"] == ["otro"]
    assert [entry["name"] for entry in result["items"]] == ["facturas", "infra", "otro"]


def test_repo_ausente_se_lista_con_la_grafia_del_inventario(section):
    result = summary(lambda name: section(name, inventory_repos=["facturas", "infra", "Legacy"]))

    assert result["missing_from_token"] == ["Legacy"]
