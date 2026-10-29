"""Regresiones de la revisión interna r1: sonda, usuarios ilegibles, cuota y nombres."""

import httpx
import pytest

from ens_evidence.connectors.google_workspace import sections
from ens_evidence.connectors.google_workspace.checks import admin_2sv_enforced, user_2sv_enrolled
from ens_evidence.connectors.google_workspace.sections import USER_FIELDS
from ens_evidence.models.enums import CheckStatus

from .check_data import EVIDENCE_ID, NOW
from .fake_api import (
    ANA,
    ASSIGNMENTS,
    CUSTOMER,
    LUIS,
    USERS,
    denied,
    google_error,
    token_error,
    user,
)

NOT_A_REJECTION = "no se pudo verificar la credencial ni el cliente (no es un rechazo): "


def test_fallo_de_red_en_la_sonda_inicial_aborta_con_motivo_de_no_verificable(api, collect):
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connect-CANARY", request=request)

    api.set(CUSTOMER, boom)

    result = collect()

    assert result.collection_status == "ERROR"
    assert result.data["error"].startswith(NOT_A_REJECTION)
    assert api.api_calls(USERS) == []


def test_un_rechazo_de_la_credencial_se_distingue_de_un_fallo_transitorio(api, collect):
    api.set("/token", token_error("invalid_grant"))

    error = collect().data["error"]

    assert "credencial revocada" in error
    assert NOT_A_REJECTION not in error


@pytest.mark.parametrize("reply", [denied(), google_error(503, "backendError")])
def test_fallo_de_la_sonda_final_invalida_la_recogida(api, collect, reply):
    api.sequence(
        CUSTOMER, [(200, {"id": "C0abc1234", "customerDomain": "taller-norte.example"}), reply]
    )

    result = collect()

    assert result.collection_status == "ERROR"
    assert result.data["error"].startswith(NOT_A_REJECTION)


def test_la_sonda_final_con_otro_cliente_invalida_la_recogida(api, collect):
    api.sequence(
        CUSTOMER,
        [
            {"id": "C0abc1234", "customerDomain": "taller-norte.example"},
            {"id": "C0otro999", "customerDomain": "taller-norte.example"},
        ],
    )

    result = collect()

    assert result.collection_status == "ERROR"
    assert result.data["error"] == "el cliente de Workspace cambió durante la recogida"


def test_cliente_distinto_del_configurado_aborta_con_su_propio_tipo_de_error(api, collect):
    api.set(
        CUSTOMER.replace("my_customer", "C0abc1234"),
        {"id": "C0otro999", "customerDomain": "taller-norte.example"},
    )

    result = collect(customer="C0abc1234")

    assert result.collection_status == "ERROR"
    assert result.data["error"] == "el cliente de Workspace no coincide con la configuración"


def test_el_error_de_cliente_no_es_un_rechazo_de_credencial():
    from ens_evidence.connectors.google_workspace.auth import CollectionAborted, CredentialRejected

    assert issubclass(CredentialRejected, CollectionAborted)
    assert not issubclass(CollectionAborted, CredentialRejected)


@pytest.mark.parametrize("email", ["o'brien@taller-norte.example", "a+b_c%d@taller-norte.example"])
def test_usernames_validos_de_google_no_invalidan_la_seccion(api, section, email):
    api.set(USERS, {"users": [user(ANA) | {"primaryEmail": email}]})

    users = section("users")

    assert users["status"] == "SUCCESS"
    assert users["items"][0]["email"] == email


def test_un_usuario_raro_queda_ilegible_y_el_resto_se_recoge(api, section):
    api.set(USERS, {"users": [user(ANA), user(LUIS) | {"primaryEmail": "sin-arroba"}]})

    users = section("users")

    assert users["status"] == "SUCCESS"
    assert users["items"][0]["id"] == ANA
    assert users["items"][1] == {"id": LUIS, "unreadable": True}
    assert users["summary"] == {"count": 2, "unreadable": 1}


def test_un_id_repetido_entre_un_usuario_ilegible_y_uno_legible_invalida_la_seccion(api, section):
    api.set(USERS, {"users": [user(ANA) | {"isAdmin": "yes"}, user(ANA)]})

    users = section("users")

    assert users["status"] == "ERROR"


def test_un_usuario_ilegible_deja_admins_incompleto_sin_consultar_sus_roles(api, section):
    api.set(USERS, {"users": [user(ANA), user(LUIS) | {"isAdmin": "yes"}]})

    admins = section("admins")

    assert admins["status"] == "ERROR"
    assert admins["summary"]["complete"] is False
    assert [r.url.params["userKey"] for r in api.api_calls(ASSIGNMENTS)] == [ANA]


def test_los_checks_no_dan_pass_ni_fail_a_un_usuario_ilegible(api, collect):
    api.set(USERS, {"users": [user(ANA), user(LUIS) | {"isEnrolledIn2Sv": "yes"}]})
    evidence = collect()

    enrolled = {r.subject: r for r in user_2sv_enrolled(evidence, EVIDENCE_ID, NOW)}
    enforced = admin_2sv_enforced(evidence, EVIDENCE_ID, NOW)

    assert enrolled[LUIS].status is CheckStatus.UNKNOWN
    assert all(r.subject != LUIS for r in enforced)
    discovery = next(r for r in enforced if r.subject is None)
    assert discovery.status in (CheckStatus.UNKNOWN, CheckStatus.ERROR)


def test_tras_un_429_en_asignaciones_no_se_consulta_a_mas_usuarios(api, section):
    api.set(ASSIGNMENTS, google_error(429, "rateLimitExceeded"))

    admins = section("admins")

    assert admins["status"] == "ERROR"
    assert len(api.api_calls(ASSIGNMENTS)) == 1
    assert all(item["roles"] is None for item in admins["items"])


def test_un_error_de_programacion_no_se_traduce_a_forma_inesperada(api, collect, monkeypatch):
    def broken(*args, **kwargs):
        raise KeyError("bug")

    monkeypatch.setattr(sections, "_user_roles", broken)

    with pytest.raises(KeyError):
        collect()


def test_last_login_no_se_pide_ni_se_guarda(api, collect, section):
    api.set(USERS, {"users": [user(ANA) | {"lastLoginTime": "2026-05-30T08:00:00.000Z"}]})

    users = section("users")

    assert "lastLoginTime" not in USER_FIELDS
    assert "last_login" not in users["items"][0]
