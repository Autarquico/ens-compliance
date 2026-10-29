import pytest

from .fake_api import ANA, DELIA, INMA, LUIS, MARTA, PACO, USERS, VIEJA, paginated, user

WHITELIST = {
    "id",
    "email",
    "suspended",
    "archived",
    "is_admin",
    "is_delegated_admin",
    "enrolled_2sv",
    "enforced_2sv",
    "has_recovery_email",
    "has_recovery_phone",
}


def by_email(users: dict, name: str) -> dict:
    return next(u for u in users["items"] if u["email"] == f"{name}@taller-norte.example")


def test_cada_usuario_se_recoge_solo_con_los_campos_de_la_lista_blanca(section):
    users = section("users")

    assert users["status"] == "SUCCESS"
    assert users["summary"] == {"count": 7, "unreadable": 0}
    assert [u["id"] for u in users["items"]] == [ANA, LUIS, MARTA, PACO, DELIA, INMA, VIEJA]
    assert all(set(u) == WHITELIST for u in users["items"])


def test_los_flags_de_seguridad_se_copian_tal_cual(section):
    users = section("users")

    assert by_email(users, "ana") == {
        "id": ANA,
        "email": "ana@taller-norte.example",
        "suspended": False,
        "archived": False,
        "is_admin": True,
        "is_delegated_admin": False,
        "enrolled_2sv": True,
        "enforced_2sv": True,
        "has_recovery_email": True,
        "has_recovery_phone": True,
    }
    luis = by_email(users, "luis")
    assert (luis["enrolled_2sv"], luis["enforced_2sv"]) == (False, False)
    assert by_email(users, "paco")["suspended"] is True
    assert by_email(users, "delia")["is_delegated_admin"] is True
    assert by_email(users, "vieja")["archived"] is True


def test_campos_ausentes_son_desconocidos_nunca_false(section):
    marta = by_email(section("users"), "marta")

    for field in (
        "is_admin",
        "is_delegated_admin",
        "enrolled_2sv",
        "enforced_2sv",
        "has_recovery_email",
        "has_recovery_phone",
    ):
        assert marta[field] is None, field
    assert marta["suspended"] is False


@pytest.mark.parametrize(
    "field", ["isEnrolledIn2Sv", "isEnforcedIn2Sv", "isAdmin", "isDelegatedAdmin", "suspended"]
)
def test_un_flag_null_es_desconocido_nunca_false(api, section, field):
    api.set(USERS, {"users": [user(ANA) | {field: None}]})

    item = section("users")["items"][0]

    key = {
        "isEnrolledIn2Sv": "enrolled_2sv",
        "isEnforcedIn2Sv": "enforced_2sv",
        "isAdmin": "is_admin",
        "isDelegatedAdmin": "is_delegated_admin",
        "suspended": "suspended",
    }[field]
    assert item[key] is None


@pytest.mark.parametrize("bad", ["false", "true", 0, 1, "", []])
@pytest.mark.parametrize("field", ["isEnrolledIn2Sv", "isEnforcedIn2Sv", "suspended", "archived"])
def test_un_booleano_mal_formado_deja_al_usuario_ilegible_en_vez_de_convertirse_en_false(
    api, section, field, bad
):
    api.set(USERS, {"users": [user(ANA) | {field: bad}]})

    users = section("users")

    assert users["status"] == "SUCCESS"
    assert users["items"] == [{"id": ANA, "unreadable": True}]
    assert users["summary"] == {"count": 1, "unreadable": 1}


def test_la_recuperacion_sale_solo_como_booleano_nunca_el_correo_ni_el_telefono(section, collect):
    users = section("users")

    assert by_email(users, "ana")["has_recovery_email"] is True
    assert by_email(users, "ana")["has_recovery_phone"] is True
    assert by_email(users, "luis")["has_recovery_email"] is False
    assert by_email(users, "luis")["has_recovery_phone"] is None
    text = str(users)
    assert "recovery-CANARY" not in text
    assert "phone-CANARY" not in text


def test_se_pide_la_proyeccion_basica_con_los_campos_limitados_a_la_lista_blanca(api, collect):
    collect()

    (params,) = api.params(USERS)

    assert params["customer"] == "my_customer"
    assert params["projection"] == "basic"
    requested = params["fields"]
    assert "users(" in requested
    for forbidden in ("name", "phones", "orgUnitPath", "addresses", "emails", "ipWhitelisted"):
        assert forbidden not in requested.replace("primaryEmail", "")


def test_ids_duplicados_invalidan_la_seccion(api, section):
    api.set(USERS, {"users": [user(ANA), user(ANA)]})

    users = section("users")

    assert users["status"] == "ERROR"
    assert users["reason"] == "usuarios duplicados en la respuesta"


@pytest.mark.parametrize("email", ["no-es-correo", "a@b", "x y@taller-norte.example", None])
def test_correo_mal_formado_o_ausente_deja_al_usuario_ilegible(api, section, email):
    api.set(USERS, {"users": [user(ANA) | {"primaryEmail": email}]})

    users = section("users")

    assert users["status"] == "SUCCESS"
    assert users["items"] == [{"id": ANA, "unreadable": True}]


def test_directorio_vacio_es_success_con_lista_vacia(api, section):
    api.set(USERS, {"kind": "admin#directory#users"})

    users = section("users")

    assert users["status"] == "SUCCESS"
    assert users["items"] == []
    assert users["summary"] == {"count": 0, "unreadable": 0}


def test_los_usuarios_de_varias_paginas_se_unen(api, section):
    api.set(USERS, paginated("users", [user(ANA), user(LUIS)], [user(MARTA)]))

    assert [u["id"] for u in section("users")["items"]] == [ANA, LUIS, MARTA]
