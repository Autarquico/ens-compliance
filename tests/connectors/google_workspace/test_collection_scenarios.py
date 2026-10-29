import json

import httpx
import pytest

from .fake_api import (
    ADMIN_EVENTS,
    ANA,
    ASSIGNMENTS,
    CUSTOMER,
    ROLES,
    TOKEN_EVENTS,
    USERS,
    FakeApi,
    denied,
    fixture,
    paginated,
    token_error,
    unauthorized,
    user,
)

SECTIONS = {"customer", "users", "admins", "oauth_token_events", "admin_events"}


def statuses(result) -> dict[str, str]:
    return {name: s["status"] for name, s in result.data["sections"].items()}


def test_una_recogida_completa_deja_todas_las_secciones_en_success(collect):
    result = collect()

    assert result.collection_status == "SUCCESS"
    assert result.data["error"] is None
    assert result.source == "google-workspace"
    assert result.asset_id == "ws-identidad"
    assert statuses(result) == dict.fromkeys(SECTIONS, "SUCCESS")


def test_token_rechazado_al_inicio_da_error_global_sin_secciones_ni_llamadas_a_la_api(
    api: FakeApi, collect
):
    api.set("/token", token_error("invalid_grant"))

    result = collect()

    assert result.collection_status == "ERROR"
    assert result.data["error"] == "HTTP 400: credencial revocada o no válida (invalid_grant)"
    assert result.data["sections"] == {}
    assert api.api_calls() == []


@pytest.mark.parametrize(
    ("code", "fragment"),
    [
        ("unauthorized_client", "delegación en todo el dominio no autorizada"),
        ("invalid_scope", "scope no autorizado"),
        ("invalid_client", "cuenta de servicio no válida"),
        ("access_denied", "acceso denegado"),
        ("algo_nuevo", "token rechazado"),
    ],
)
def test_cada_rechazo_del_endpoint_de_token_da_error_global_con_motivo_fijo(
    api, collect, code, fragment
):
    api.set("/token", token_error(code, "descripcion-CANARY"))

    result = collect()

    assert result.collection_status == "ERROR"
    assert fragment in result.data["error"]
    assert "CANARY" not in result.data["error"]
    assert api.api_calls() == []


@pytest.mark.parametrize("status", [429, 500, 503])
def test_endpoint_de_token_no_disponible_da_error_global_sin_confundirlo_con_un_rechazo(
    api, collect, status
):
    api.set("/token", (status, {"error": "backend_error"}))

    result = collect()

    assert result.collection_status == "ERROR"
    assert result.data["error"] == (
        "no se pudo verificar la credencial ni el cliente (no es un rechazo): "
        f"HTTP {status}: el endpoint de token no está disponible"
    )
    assert result.data["sections"] == {}
    assert api.api_calls() == []


def test_respuesta_de_token_sin_access_token_da_error_global(api, collect):
    api.set("/token", {"expires_in": 3600})

    result = collect()

    assert result.collection_status == "ERROR"
    assert result.data["error"] == "el endpoint de token devolvió una respuesta no válida"


def test_401_en_la_primera_llamada_de_la_api_da_error_global(api, collect):
    api.set(CUSTOMER, unauthorized())

    result = collect()

    assert result.collection_status == "ERROR"
    assert result.data["error"] == "HTTP 401: credencial rechazada"


def test_revocacion_a_mitad_tras_paginas_correctas_da_error_global(api, collect):
    page = [user(ANA)]
    first_page = paginated("users", page, page)

    def revoked_after_first_page(request):
        if request.url.params.get("pageToken") is None:
            return first_page(request)
        return httpx.Response(401, json=unauthorized()[1])

    api.set(USERS, revoked_after_first_page)

    result = collect()

    assert result.collection_status == "ERROR"
    assert result.data["error"] == "HTTP 401: credencial rechazada"
    assert len(api.api_calls(USERS)) == 2
    assert "admins" not in result.data["sections"]


@pytest.mark.parametrize("path", [USERS, ROLES, ASSIGNMENTS, TOKEN_EVENTS, ADMIN_EVENTS])
def test_401_en_cualquier_endpoint_da_error_global(api, collect, path):
    api.set(path, unauthorized())

    result = collect()

    assert result.collection_status == "ERROR"
    assert "credencial rechazada" in result.data["error"]


def test_401_en_la_sonda_final_invalida_la_recogida_aunque_todo_lo_anterior_fuera_bien(
    api, collect
):
    api.sequence(CUSTOMER, [fixture("customer"), unauthorized()])

    result = collect()

    assert result.collection_status == "ERROR"
    assert result.data["error"] == "HTTP 401: credencial rechazada"
    assert statuses(result)["users"] == "SUCCESS"


def test_las_secciones_recogidas_antes_del_error_global_se_conservan_pero_el_global_manda(
    api, collect
):
    api.set(TOKEN_EVENTS, unauthorized())

    result = collect()

    assert result.collection_status == "ERROR"
    assert statuses(result)["users"] == "SUCCESS"
    assert "oauth_token_events" not in result.data["sections"]


def test_403_en_un_endpoint_deja_esa_seccion_en_permission_denied_sin_invalidar_la_credencial(
    api, collect
):
    api.set(TOKEN_EVENTS, denied())

    result = collect()

    assert result.collection_status == "SUCCESS"
    assert result.data["error"] is None
    assert statuses(result) == {
        **dict.fromkeys(SECTIONS, "SUCCESS"),
        "oauth_token_events": "PERMISSION_DENIED",
    }
    denied_section = result.data["sections"]["oauth_token_events"]
    assert denied_section["items"] == []
    assert denied_section["summary"]["complete"] is False
    assert denied_section["reason"] == "HTTP 403: permiso insuficiente"


def test_sin_permiso_es_distinto_de_no_configurado(api, collect):
    api.set(TOKEN_EVENTS, denied())
    api.set(ADMIN_EVENTS, {"kind": "admin#reports#activities"})

    sections = collect().data["sections"]

    assert sections["oauth_token_events"]["status"] == "PERMISSION_DENIED"
    assert sections["admin_events"]["status"] == "SUCCESS"
    assert sections["admin_events"]["items"] == []
    assert sections["admin_events"]["summary"]["activities"] == 0


def test_403_en_users_deja_users_y_admins_en_permission_denied_y_sigue_recogiendo_el_resto(
    api, collect
):
    api.set(USERS, denied())

    result = collect()

    assert result.collection_status == "SUCCESS"
    assert statuses(result) == {
        **dict.fromkeys(SECTIONS, "SUCCESS"),
        "users": "PERMISSION_DENIED",
        "admins": "PERMISSION_DENIED",
    }
    assert api.api_calls(ASSIGNMENTS) == []


def test_403_en_la_sonda_de_cliente_da_error_global_y_no_recoge_nada(api, collect):
    api.set(CUSTOMER, denied())

    result = collect()

    assert result.collection_status == "ERROR"
    assert "no se pudo verificar" in result.data["error"]
    assert "permiso insuficiente" in result.data["error"]
    assert result.data["sections"] == {}
    assert api.api_calls(USERS) == []


def test_el_resultado_se_serializa_a_json(collect):
    result = collect()

    assert json.loads(result.model_dump_json())["data"]["sections"]["users"]["status"] == "SUCCESS"
