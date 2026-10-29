import httpx
import pytest

from ens_evidence.connectors.google_workspace import sections

from .fake_api import (
    ANA,
    ASSIGNMENTS,
    DELIA,
    INMA,
    LUIS,
    ROLES,
    USERS,
    denied,
    google_error,
    paginated,
    user,
)

SUPER_ADMIN = "9100000000000001"
SUPPORT = "9100000000000002"


def admin(section_data: dict, user_id: str) -> dict:
    return next(item for item in section_data["items"] if item["user_id"] == user_id)


def test_se_une_cada_usuario_con_sus_flags_y_roles_por_id_inmutable(section):
    admins = section("admins")

    assert admins["status"] == "SUCCESS"
    assert admins["summary"] == {
        "complete": True,
        "users": 7,
        "roles_catalog": {"status": "SUCCESS"},
    }
    assert admin(admins, ANA) == {
        "user_id": ANA,
        "is_admin": True,
        "is_delegated_admin": False,
        "status": "SUCCESS",
        "roles": [
            {
                "role_id": SUPER_ADMIN,
                "role_name": "_SEED_ADMIN_ROLE",
                "super_admin_role": True,
                "direct": True,
                "scope_type": "CUSTOMER",
            }
        ],
    }


def test_un_admin_delegado_trae_su_rol_con_el_ambito_de_unidad_organizativa(section):
    delia = admin(section("admins"), DELIA)

    assert delia["is_delegated_admin"] is True
    assert delia["roles"] == [
        {
            "role_id": SUPPORT,
            "role_name": "Soporte de usuarios",
            "super_admin_role": False,
            "direct": True,
            "scope_type": "ORG_UNIT",
        }
    ]


def test_un_rol_heredado_por_grupo_aparece_como_indirecto_aunque_los_flags_sean_false(section):
    inma = admin(section("admins"), INMA)

    assert (inma["is_admin"], inma["is_delegated_admin"]) == (False, False)
    assert [(r["role_id"], r["direct"]) for r in inma["roles"]] == [(SUPPORT, False)]


def test_no_es_admin_exige_consulta_correcta_y_lista_de_roles_vacia(section):
    luis = admin(section("admins"), LUIS)

    assert luis["status"] == "SUCCESS"
    assert luis["roles"] == []
    assert luis["is_admin"] is False


def test_las_asignaciones_se_piden_con_roles_indirectos_por_id_de_usuario(api, collect):
    collect()

    params = api.params(ASSIGNMENTS)

    assert len(params) == 7
    assert {p["userKey"] for p in params} >= {ANA, INMA}
    assert {p["includeIndirectRoleAssignments"] for p in params} == {"true"}


def test_no_aparecen_correos_ni_nombres_ni_descripciones_en_admins(section):
    text = str(section("admins"))

    assert "@" not in text
    assert "CANARY" not in text


def test_asignacion_con_ambito_desconocido_se_sustituye_por_other(api, section):
    api.set(
        ASSIGNMENTS,
        {
            "items": [
                {"roleId": SUPPORT, "assignedTo": ANA, "scopeType": "ambito-CANARY"},
            ]
        },
    )

    roles = admin(section("admins"), ANA)["roles"]

    assert roles[0]["scope_type"] == "other"
    assert "CANARY" not in str(roles)


def test_rol_fuera_del_catalogo_conserva_el_id_y_deja_nombre_y_super_admin_desconocidos(
    api, section
):
    api.set(ASSIGNMENTS, {"items": [{"roleId": "777", "assignedTo": ANA, "scopeType": "CUSTOMER"}]})

    (role,) = admin(section("admins"), ANA)["roles"]

    assert (role["role_id"], role["role_name"], role["super_admin_role"]) == ("777", None, None)


def test_403_al_consultar_asignaciones_deja_roles_desconocidos_y_deja_de_consultar(api, section):
    api.set(ASSIGNMENTS, denied())

    admins = section("admins")

    assert admins["status"] == "PERMISSION_DENIED"
    assert admins["summary"]["complete"] is False
    assert all(item["roles"] is None for item in admins["items"])
    assert {item["status"] for item in admins["items"]} == {"PERMISSION_DENIED"}
    assert len(api.api_calls(ASSIGNMENTS)) == 1


def test_403_en_el_catalogo_de_roles_marca_la_seccion_incompleta_pero_conserva_los_roles(
    api, section
):
    api.set(ROLES, denied())

    admins = section("admins")

    assert admins["status"] == "PERMISSION_DENIED"
    assert admins["summary"]["complete"] is False
    assert admins["summary"]["roles_catalog"] == {"status": "PERMISSION_DENIED"}
    (role,) = admin(admins, ANA)["roles"]
    assert (role["role_id"], role["role_name"]) == (SUPER_ADMIN, None)


def test_error_en_las_asignaciones_de_un_usuario_solo_deja_a_ese_usuario_desconocido(api, section):
    def flaky(request: httpx.Request) -> httpx.Response:
        if request.url.params["userKey"] == LUIS:
            return httpx.Response(429, json=google_error(429, "rateLimitExceeded")[1])
        return httpx.Response(200, json={})

    api.set(ASSIGNMENTS, flaky)

    admins = section("admins")

    assert admins["status"] == "ERROR"
    assert admins["summary"]["complete"] is False
    assert admin(admins, LUIS)["roles"] is None
    assert admin(admins, LUIS)["status"] == "ERROR"
    assert admin(admins, ANA)["roles"] == []
    assert admin(admins, ANA)["status"] == "SUCCESS"


def test_asignaciones_truncadas_por_un_fallo_en_la_segunda_pagina_dejan_roles_desconocidos(
    api, section
):
    def truncated(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("pageToken") is None:
            return httpx.Response(
                200,
                json={
                    "items": [{"roleId": SUPPORT, "assignedTo": ANA}],
                    "nextPageToken": "page-1",
                },
            )
        return httpx.Response(500, json=google_error(500, "backendError")[1])

    api.set(ASSIGNMENTS, truncated)

    admins = section("admins")

    assert admin(admins, ANA)["roles"] is None
    assert admin(admins, ANA)["status"] == "ERROR"
    assert admins["summary"]["complete"] is False


def test_pagina_vacia_con_token_sigue_paginando_asignaciones(api, section):
    assignment = {"roleId": SUPPORT, "assignedTo": ANA, "scopeType": "CUSTOMER"}
    api.set(ASSIGNMENTS, paginated("items", [], [assignment]))

    ana = admin(section("admins"), ANA)

    assert [r["role_id"] for r in ana["roles"]] == [SUPPORT]


def test_superado_el_tope_de_consultas_el_resto_queda_desconocido(api, section, monkeypatch):
    monkeypatch.setattr(sections, "MAX_ROLE_QUERIES", 2)

    admins = section("admins")

    assert len(api.api_calls(ASSIGNMENTS)) == 2
    assert admins["status"] == "ERROR"
    assert admins["summary"]["complete"] is False
    unknown = [item for item in admins["items"] if item["roles"] is None]
    assert len(unknown) == 5
    assert {item["status"] for item in unknown} == {"ERROR"}


@pytest.mark.parametrize("status", ["PERMISSION_DENIED", "ERROR"])
def test_si_users_falla_admins_hereda_el_estado_sin_consultar_roles(api, section, status):
    api.set(USERS, denied() if status == "PERMISSION_DENIED" else (500, {}))

    admins = section("admins")

    assert admins["status"] == status
    assert admins["items"] == []
    assert admins["summary"] == {"complete": False}
    assert admins["reason"].startswith("depende de users")
    assert api.api_calls(ASSIGNMENTS) == []
    assert api.api_calls(ROLES) == []


def test_usuario_con_flags_ausentes_conserva_los_roles_consultados(api, section):
    api.set(USERS, {"users": [user(ANA) | {"isAdmin": None}]})

    ana = admin(section("admins"), ANA)

    assert ana["is_admin"] is None
    assert ana["roles"][0]["role_id"] == SUPER_ADMIN
