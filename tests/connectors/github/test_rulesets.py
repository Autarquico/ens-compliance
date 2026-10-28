import pytest

from .fake_api import FACTURAS, ORG, REPO, denied, fixture, repo_body, variant
from .test_permissions import item


def rulesets(section) -> list[dict]:
    return item(section("branch_rules"))["rulesets"]["items"]


def detail(ruleset_id: int, **changes):
    return {**variant("ruleset_detail", **changes), "id": ruleset_id}


def test_el_inventario_pide_los_rulesets_heredados_y_su_detalle(api, section):
    section("branch_rules")

    listing = api.params(rf"{FACTURAS}/rulesets")
    details = api.params(rf"{FACTURAS}/rulesets/\d+")
    assert listing[0]["includes_parents"] == "true"
    assert len(details) == 3
    assert all(call["includes_parents"] == "true" for call in details)


def test_el_inventario_incluye_rulesets_evaluate_y_disabled_con_su_origen(section):
    found = rulesets(section)

    assert [(r["id"], r["source_type"], r["enforcement"]) for r in found] == [
        (11, "Repository", "active"),
        (12, "Organization", "evaluate"),
        (13, "Repository", "disabled"),
    ]
    assert all(r["target"] == "branch" for r in found)


def test_bypass_actors_ausente_no_es_lista_vacia(api, section):
    api.set(rf"{FACTURAS}/rulesets/11", detail(11, bypass_actors=None))

    first = rulesets(section)[0]

    assert first["bypass_actors"] is None
    assert first["status"] == "SUCCESS"


def test_bypass_actors_null_no_es_lista_vacia(api, section):
    api.set(rf"{FACTURAS}/rulesets/11", {**detail(11), "bypass_actors": None})

    assert rulesets(section)[0]["bypass_actors"] is None


def test_bypass_actors_vacio_demostrado_es_lista_vacia(api, section):
    api.set(rf"{FACTURAS}/rulesets/11", detail(11, bypass_actors=[]))

    assert rulesets(section)[0]["bypass_actors"] == []


def test_bypass_actors_con_always_y_exempt_se_conservan_sin_ids_ni_nombres(api, section):
    actors = [
        {"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"},
        {"actor_id": 99, "actor_type": "Team", "bypass_mode": "exempt", "name": "equipo-CANARY"},
        {"actor_id": 7, "actor_type": "Integration", "bypass_mode": "pull_request"},
    ]
    api.set(rf"{FACTURAS}/rulesets/11", detail(11, bypass_actors=actors))

    assert rulesets(section)[0]["bypass_actors"] == [
        {"actor_type": "RepositoryRole", "bypass_mode": "always"},
        {"actor_type": "Team", "bypass_mode": "exempt"},
        {"actor_type": "Integration", "bypass_mode": "pull_request"},
    ]


def test_bypass_mode_o_actor_desconocidos_se_reducen_a_other(api, section):
    actors = [{"actor_type": "Nuevo-CANARY", "bypass_mode": "modo-CANARY"}]
    api.set(rf"{FACTURAS}/rulesets/11", detail(11, bypass_actors=actors))

    assert rulesets(section)[0]["bypass_actors"] == [
        {"actor_type": "other", "bypass_mode": "other"}
    ]


def test_bypass_actors_mal_formado_invalida_el_detalle_de_ese_ruleset(api, section):
    api.set(rf"{FACTURAS}/rulesets/11", detail(11, bypass_actors="siempre"))

    first = rulesets(section)[0]

    assert first["status"] == "ERROR"
    assert first["bypass_actors"] is None
    assert rulesets(section)[1]["status"] == "SUCCESS"


def test_detalle_sin_permiso_deja_ese_ruleset_sin_demostrar_y_el_resto_intacto(api, section):
    api.set(rf"{FACTURAS}/rulesets/12", denied())

    result = section("branch_rules")

    found = item(result)["rulesets"]
    assert found["status"] == "PERMISSION_DENIED"
    assert found["items"][1]["status"] == "PERMISSION_DENIED"
    assert found["items"][1]["bypass_actors"] is None
    assert found["items"][1]["enforcement"] == "evaluate"
    assert found["items"][0]["status"] == "SUCCESS"
    assert result["status"] == "PERMISSION_DENIED"


def test_inventario_de_rulesets_sin_permiso_no_es_sin_rulesets(api, section):
    api.set(rf"{FACTURAS}/rulesets", denied())

    entry = item(section("branch_rules"))

    assert entry["rulesets"]["status"] == "PERMISSION_DENIED"
    assert entry["rulesets"]["items"] == []
    assert entry["status"] == "PERMISSION_DENIED"


def test_inventario_de_rulesets_vacio_demostrado_es_success(api, section):
    api.set(rf"{FACTURAS}/rulesets", [])

    assert item(section("branch_rules"))["rulesets"] == {"status": "SUCCESS", "items": []}


@pytest.mark.parametrize("field", ["enforcement", "source_type", "target"])
def test_valores_libres_de_un_ruleset_se_reducen_a_other(api, section, field):
    listing = fixture("rulesets")
    listing[0][field] = "valor-CANARY"
    api.set(rf"{FACTURAS}/rulesets", listing)

    assert rulesets(section)[0][field] == "other"


def test_ruleset_sin_id_entero_invalida_el_inventario(api, section):
    api.set(rf"{FACTURAS}/rulesets", [{"id": "11", "enforcement": "active"}])

    assert item(section("branch_rules"))["rulesets"]["status"] == "ERROR"


def test_reglas_efectivas_conservan_la_procedencia_de_cada_regla(section):
    effective = item(section("branch_rules"))["effective_rules"]

    assert effective["branch"] == "main"
    assert effective["rules"][0] == {
        "type": "pull_request",
        "ruleset_id": 11,
        "ruleset_source_type": "Repository",
    }
    assert {rule["type"] for rule in effective["rules"]} == {
        "pull_request",
        "non_fast_forward",
        "deletion",
    }


def test_reglas_efectivas_de_un_ruleset_de_la_organizacion_conservan_su_origen(api, section):
    rules = [{"type": "pull_request", "ruleset_source_type": "Organization", "ruleset_id": 12}]
    api.set(rf"{REPO}/rules/branches/main", rules)

    effective = item(section("branch_rules"))["effective_rules"]

    assert effective["rules"] == [
        {"type": "pull_request", "ruleset_id": 12, "ruleset_source_type": "Organization"}
    ]


def test_reglas_efectivas_vacias_demostradas_son_lista_vacia(api, section):
    api.set(rf"{REPO}/rules/branches/main", [])

    assert item(section("branch_rules"))["effective_rules"] == {
        "status": "SUCCESS",
        "branch": "main",
        "rules": [],
    }


def test_reglas_efectivas_sin_permiso_no_son_sin_reglas(api, section):
    api.set(rf"{REPO}/rules/branches/main", denied())

    effective = item(section("branch_rules"))["effective_rules"]

    assert effective["status"] == "PERMISSION_DENIED"
    assert effective["rules"] == []


def test_tipo_de_regla_desconocido_se_reduce_a_other(api, section):
    api.set(rf"{REPO}/rules/branches/main", [{"type": "Tipo Libre-CANARY"}])

    (rule,) = item(section("branch_rules"))["effective_rules"]["rules"]

    assert rule["type"] == "other"


def test_la_rama_por_defecto_con_barra_se_codifica_en_la_ruta(api, section):
    api.set(f"/orgs/{ORG}/repos", [repo_body(default_branch="feat/x y")])
    api.set(rf"{REPO}/rules/branches/.*", [])
    api.set(rf"{REPO}/branches/.+/protection", (404, {"message": "Branch not protected"}))

    section("branch_rules")

    raw = [call.url.raw_path.decode() for call in api.calls]
    assert any(p.startswith(f"{FACTURAS}/rules/branches/feat%2Fx%20y") for p in raw)
    assert any(p.startswith(f"{FACTURAS}/branches/feat%2Fx%20y/protection") for p in raw)


@pytest.mark.parametrize("branch", ["..", "."])
def test_rama_por_defecto_que_recorre_la_ruta_no_se_interpola(api, section, branch):
    api.set(f"/orgs/{ORG}/repos", [repo_body(default_branch=branch)])

    entry = item(section("branch_rules"))

    assert entry["effective_rules"]["status"] == "ERROR"
    assert entry["classic"]["status"] == "ERROR"
    assert not any("/rules/branches" in path for path in api.paths())
