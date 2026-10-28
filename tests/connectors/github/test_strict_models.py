import pytest

from .fake_api import ORG, REPO, fixture, repo_body, variant
from .test_permissions import item


def org_with(**fields):
    return {**fixture("org"), **fields}


def two_factor(section) -> dict:
    return section("organization")["summary"]["two_factor_requirement"]


@pytest.mark.parametrize("value", ["false", "true", "False", 0, 1, "", "yes"])
def test_2fa_mal_formado_no_se_coacciona_a_booleano(api, section, value):
    api.set(f"/orgs/{ORG}", org_with(two_factor_requirement_enabled=value))

    requirement = two_factor(section)

    assert requirement["status"] == "ERROR"
    assert requirement["enabled"] is None


def test_2fa_de_otra_organizacion_no_se_acepta(api, section):
    api.set(f"/orgs/{ORG}", org_with(login="otra-org"))

    requirement = two_factor(section)

    assert requirement["status"] == "ERROR"
    assert requirement["enabled"] is None


def test_organizacion_sin_login_no_se_acepta(api, section):
    api.set(f"/orgs/{ORG}", variant("org", login=None))

    assert two_factor(section)["enabled"] is None


def test_el_login_de_la_organizacion_se_compara_sin_distinguir_mayusculas(api, section):
    api.set(f"/orgs/{ORG}", org_with(login="Taller-Norte"))

    assert two_factor(section)["enabled"] is True


def test_miembros_sin_2fa_mal_formados_no_dan_recuento(api, section):
    api.set(f"/orgs/{ORG}/members", ["no-es-un-objeto"])

    members = section("organization")["summary"]["members_without_two_factor"]

    assert members["status"] == "ERROR"
    assert members["count"] is None


def test_miembros_sin_2fa_como_objeto_no_dan_recuento(api, section):
    api.set(f"/orgs/{ORG}/members", {"members": []})

    members = section("organization")["summary"]["members_without_two_factor"]

    assert members["status"] == "ERROR"
    assert members["count"] is None


def test_miembros_sin_2fa_vacio_demostrado_es_cero(api, section):
    api.set(f"/orgs/{ORG}/members", [])

    members = section("organization")["summary"]["members_without_two_factor"]

    assert members == {"status": "SUCCESS", "count": 0}


@pytest.mark.parametrize(
    "broken",
    [
        {"archived": "false"},
        {"archived": 0},
        {"name": None},
        {"name": ".."},
        {"name": "a b"},
        {"permissions": {"admin": "true"}},
        {"default_branch": ""},
    ],
    ids=[
        "archived-str",
        "archived-int",
        "name-ausente",
        "name-punto",
        "name-espacio",
        "admin-str",
        "rama-vacia",
    ],
)
def test_repositorio_mal_formado_invalida_el_inventario_y_no_se_rellena(api, collect, broken):
    api.set(f"/orgs/{ORG}/repos", [repo_body("infra"), repo_body(**broken)])

    sections = collect().data["sections"]

    assert sections["repositories"]["status"] == "ERROR"
    assert sections["repositories"]["items"] == []
    assert sections["dependabot"]["status"] == "ERROR"
    assert sections["secret_scanning"]["items"] == []


def test_campos_omitidos_o_null_del_repositorio_quedan_en_null_nunca_en_false(api, section):
    api.set(
        f"/orgs/{ORG}/repos",
        [
            repo_body(
                archived=None,
                visibility=None,
                default_branch=None,
                permissions=None,
                security_and_analysis=None,
            ),
            {**repo_body("infra"), "archived": None, "permissions": {"admin": None}},
        ],
    )

    repositories = section("repositories")

    for entry in repositories["items"]:
        assert entry["archived"] is None
        assert entry["admin"] is None
    assert repositories["items"][0]["visibility"] is None
    assert repositories["items"][0]["default_branch"] is None
    assert repositories["items"][0]["security_and_analysis_visible"] is False
    assert repositories["items"][0]["secret_scanning"] is None


def test_repositorio_sin_rama_por_defecto_no_inventa_una(api, section):
    api.set(f"/orgs/{ORG}/repos", [repo_body(default_branch=None)])

    entry = item(section("branch_rules"))

    assert entry["effective_rules"]["status"] == "ERROR"
    assert entry["classic"]["status"] == "ERROR"
    assert entry["classic"]["state"] == "unknown"
    assert not any("/rules/branches/" in path for path in api.paths())


def test_visibilidad_desconocida_se_reduce_a_other(api, section):
    api.set(f"/orgs/{ORG}/repos", [repo_body(visibility="texto-libre")])

    assert section("repositories")["items"][0]["visibility"] == "other"


def test_proteccion_clasica_sin_campos_explicitos_deja_null_en_vez_de_false(api, section):
    api.set(rf"{REPO}/branches/main/protection", {"url": "x"})

    classic = item(section("branch_rules"))["classic"]

    assert classic == {
        "status": "SUCCESS",
        "state": "protected",
        "requires_pull_request": False,
        "bypass_pull_request_allowances": False,
        "enforce_admins": None,
        "allow_force_pushes": None,
        "allow_deletions": None,
    }


@pytest.mark.parametrize("field", ["enforce_admins", "allow_force_pushes", "allow_deletions"])
@pytest.mark.parametrize("value", ["false", "true", 0, 1, None])
def test_proteccion_clasica_con_flag_mal_formado_es_error(api, section, field, value):
    body = {**fixture("protection"), field: {"enabled": value}}
    api.set(rf"{REPO}/branches/main/protection", body)

    classic = item(section("branch_rules"))["classic"]

    if value is None:
        assert classic[field] is None
    else:
        assert classic["status"] == "ERROR"
        assert classic["state"] == "unknown"


def test_proteccion_clasica_con_excepciones_de_pull_request_las_marca(api, section):
    body = fixture("protection")
    body["required_pull_request_reviews"]["bypass_pull_request_allowances"]["teams"] = [
        {"slug": "equipo-CANARY"}
    ]
    api.set(rf"{REPO}/branches/main/protection", body)

    classic = item(section("branch_rules"))["classic"]

    assert classic["bypass_pull_request_allowances"] is True
    assert classic["requires_pull_request"] is True


def test_proteccion_clasica_sin_revision_de_pull_request_no_la_exige(api, section):
    body = variant("protection", required_pull_request_reviews=None)
    api.set(rf"{REPO}/branches/main/protection", body)

    classic = item(section("branch_rules"))["classic"]

    assert classic["requires_pull_request"] is False


@pytest.mark.parametrize("permissions", [{"admin": "true"}, {"push": 1}])
def test_colaborador_con_permisos_mal_formados_invalida_la_lectura(api, section, permissions):
    api.set(rf"{REPO}/collaborators", [{"permissions": permissions}])

    entry = item(section("collaborators"))

    assert entry["status"] == "ERROR"
    assert entry["outside_total"] is None


def test_colaborador_sin_permisos_se_cuenta_como_desconocido(api, section):
    api.set(rf"{REPO}/collaborators", [{"login": "x"}, {"login": "y", "permissions": None}])

    entry = item(section("collaborators"))

    assert entry["outside_total"] == 2
    assert entry["by_permission"]["unknown"] == 2


def test_colaboradores_se_cuentan_por_permiso_mas_alto_sin_logins(section):
    entry = item(section("collaborators"))

    assert entry["outside_total"] == 2
    assert entry["by_permission"] == {
        "admin": 0,
        "maintain": 0,
        "push": 1,
        "triage": 0,
        "pull": 1,
        "unknown": 0,
    }


@pytest.mark.parametrize("read_only", ["true", 1, None])
def test_deploy_key_con_read_only_mal_formado_o_null_invalida_la_lectura(api, section, read_only):
    api.set(rf"{REPO}/keys", [{"read_only": read_only, "created_at": "2026-01-01T00:00:00Z"}])

    entry = item(section("deploy_keys"))

    assert entry["status"] == "ERROR"
    assert entry["writable_count"] is None


def test_deploy_key_sin_read_only_invalida_la_lectura(api, section):
    api.set(rf"{REPO}/keys", [{"created_at": "2026-01-01T00:00:00Z"}])

    assert item(section("deploy_keys"))["status"] == "ERROR"


def test_deploy_keys_cuentan_las_escribibles_y_normalizan_la_fecha_a_utc(section):
    entry = item(section("deploy_keys"))

    assert entry["count"] == 2
    assert entry["writable_count"] == 1
    assert entry["keys"] == [
        {"read_only": True, "created_at": "2026-01-15T10:00:00Z"},
        {"read_only": False, "created_at": "2026-02-01T07:30:00Z"},
    ]


def test_deploy_key_con_fecha_mal_formada_invalida_la_lectura(api, section):
    api.set(rf"{REPO}/keys", [{"read_only": True, "created_at": "ayer"}])

    assert item(section("deploy_keys"))["status"] == "ERROR"


@pytest.mark.parametrize("field", ["enabled", "can_approve_pull_request_reviews"])
def test_permisos_de_actions_mal_formados_no_se_coaccionan(api, section, field):
    endpoint = "actions_permissions" if field == "enabled" else "workflow_permissions"
    suffix = "" if field == "enabled" else "/workflow"
    api.set(rf"{REPO}/actions/permissions{suffix}", variant(endpoint, **{field: "false"}))

    entry = item(section("actions"))

    sub = entry["permissions" if field == "enabled" else "workflow"]
    assert sub["status"] == "ERROR"
    assert sub[field] is None


def test_actions_omitido_queda_en_null_y_las_dos_lecturas_tienen_estado_propio(api, section):
    api.set(rf"{REPO}/actions/permissions", {})
    api.set(rf"{REPO}/actions/permissions/workflow", (403, {"message": "x"}))

    entry = item(section("actions"))

    assert entry["permissions"] == {"status": "SUCCESS", "enabled": None, "allowed_actions": None}
    assert entry["workflow"]["status"] == "PERMISSION_DENIED"
    assert entry["status"] == "PERMISSION_DENIED"


def test_actions_recoge_los_permisos_del_repositorio(section):
    entry = item(section("actions"))

    assert entry["permissions"] == {
        "status": "SUCCESS",
        "enabled": True,
        "allowed_actions": "selected",
    }
    assert entry["workflow"] == {
        "status": "SUCCESS",
        "default_workflow_permissions": "read",
        "can_approve_pull_request_reviews": False,
    }


def test_entornos_guardan_reglas_y_numero_de_revisores_nunca_los_revisores(section):
    entry = item(section("environments"))

    assert entry["environments"] == [
        {
            "name": "production",
            "protection_rule_types": ["required_reviewers", "wait_timer"],
            "required_reviewers": 1,
            "restricts_branches": True,
        }
    ]


def test_entorno_mal_formado_invalida_la_lectura(api, section):
    api.set(rf"{REPO}/environments", {"environments": [{"protection_rules": []}]})

    entry = item(section("environments"))

    assert entry["status"] == "ERROR"
    assert entry["environments"] is None


def test_entornos_sin_la_clave_de_lista_es_error_no_lista_vacia(api, section):
    api.set(rf"{REPO}/environments", {"total_count": 0})

    entry = item(section("environments"))

    assert entry["status"] == "ERROR"
    assert entry["environments"] is None


def test_entornos_vacios_demostrados_son_lista_vacia(api, section):
    api.set(rf"{REPO}/environments", {"total_count": 0, "environments": []})

    entry = item(section("environments"))

    assert entry["status"] == "SUCCESS"
    assert entry["environments"] == []


def test_instalacion_de_app_mal_formada_invalida_la_seccion(api, section):
    api.set(f"/orgs/{ORG}/installations", {"installations": [{"permissions": "todos"}]})

    result = section("app_installations")

    assert result["status"] == "ERROR"
    assert result["items"] == []


def test_instalaciones_guardan_slug_permisos_y_seleccion(section):
    (installation,) = section("app_installations")["items"]

    assert installation == {
        "app_slug": "dependabot",
        "repository_selection": "selected",
        "permissions": {"contents": "read", "metadata": "read"},
        "suspended": False,
    }


def test_instalacion_con_valores_libres_se_reduce_a_other(api, section):
    body = {
        "installations": [
            {
                "app_slug": "slug con espacios-CANARY",
                "repository_selection": "algo-CANARY",
                "permissions": {"contents": "nivel-CANARY", "Nombre Libre-CANARY": "read"},
                "suspended_at": "2026-01-01T00:00:00Z",
            }
        ]
    }
    api.set(f"/orgs/{ORG}/installations", body)

    (installation,) = section("app_installations")["items"]

    assert installation["app_slug"] == "other"
    assert installation["repository_selection"] == "other"
    assert installation["permissions"] == {"contents": "other"}
    assert installation["suspended"] is True
