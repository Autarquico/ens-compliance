import json

import httpx
import pytest

from .fake_api import (
    ADMIN_EVENTS,
    TOKEN_EVENTS,
    denied,
    fixture,
    google_error,
    paginated,
)

DRIVE = "https://www.googleapis.com/auth/drive"
MAIL = "https://www.googleapis.com/auth/gmail.readonly"
WINDOW = {
    "requested_start": "2026-05-02T12:00:00Z",
    "requested_end": "2026-06-01T12:00:00Z",
}


def activity(events: list[dict], actor: dict | None = None, time: str = "2026-05-20T09:00:00Z"):
    body = {"id": {"time": time}, "events": events}
    return body | ({"actor": actor} if actor else {})


def token_event(name="authorize", **parameters):
    values = {"client_id": "111.apps.googleusercontent.com", "app_name": "Hojas"} | parameters
    return {
        "type": "auth",
        "name": name,
        "parameters": [
            {"name": key, **({"multiValue": v} if isinstance(v, list) else {"value": v})}
            for key, v in values.items()
        ],
    }


def test_los_eventos_de_token_se_resumen_por_aplicacion_sin_correos(section):
    events = section("oauth_token_events")

    assert events["status"] == "SUCCESS"
    assert events["items"] == [
        {
            "client_id": "111.apps.googleusercontent.com",
            "app_name": "Hojas Externas",
            "events": {"activity": 1, "authorize": 2},
            "scopes": [DRIVE, MAIL],
            "observed_users": 2,
        },
        {
            "client_id": "222.apps.googleusercontent.com",
            "app_name": "Agenda Pro",
            "events": {"authorize": 1},
            "scopes": [MAIL],
            "observed_users": 1,
        },
    ]
    assert "@" not in json.dumps(events)


def test_la_ventana_pedida_y_la_observada_y_la_completitud_van_en_el_resumen(section):
    summary = section("oauth_token_events")["summary"]

    assert summary == {
        "complete": True,
        **WINDOW,
        "observed_first": "2026-05-20T09:00:00Z",
        "observed_last": "2026-05-28T12:00:00Z",
        "activities": 4,
        "activities_without_actor": 0,
    }


def test_se_piden_los_ultimos_30_dias_en_ambos_informes(api, collect):
    collect()

    (token,) = api.params(TOKEN_EVENTS)
    (admin,) = api.params(ADMIN_EVENTS)

    for params in (token, admin):
        assert params["startTime"] == WINDOW["requested_start"]
        assert params["endTime"] == WINDOW["requested_end"]


def test_sin_eventos_es_success_con_cero_actividades_y_no_prueba_que_no_haya_apps(api, section):
    api.set(TOKEN_EVENTS, {"kind": "admin#reports#activities"})

    events = section("oauth_token_events")

    assert events["status"] == "SUCCESS"
    assert events["items"] == []
    assert events["summary"]["activities"] == 0
    assert events["summary"]["observed_first"] is None
    assert events["summary"]["observed_last"] is None


def test_la_seccion_no_se_presenta_como_inventario_de_apps(collect):
    sections = collect().data["sections"]

    assert "oauth_token_events" in sections
    assert not {"oauth_apps", "oauth_inventory", "apps"} & set(sections)


def test_un_actor_se_cuenta_una_vez_aunque_repita_y_se_deduplica_por_perfil_o_correo(api, section):
    api.set(
        TOKEN_EVENTS,
        {
            "items": [
                activity([token_event()], {"profileId": "p1", "email": "a@taller-norte.example"}),
                activity(
                    [token_event()], {"profileId": "p1", "email": "otro@taller-norte.example"}
                ),
                activity([token_event()], {"email": "b@taller-norte.example"}),
                activity([token_event()], {"email": "b@taller-norte.example"}),
                activity([token_event()]),
            ]
        },
    )

    events = section("oauth_token_events")

    assert events["items"][0]["observed_users"] == 2
    assert events["summary"]["activities_without_actor"] == 1
    assert "@" not in json.dumps(events)


def test_valores_fuera_de_formato_se_sustituyen_por_other_o_se_recortan(api, section):
    long_name = "N" * 300
    api.set(
        TOKEN_EVENTS,
        {
            "items": [
                activity(
                    [
                        token_event(
                            name="evento con espacios-CANARY",
                            client_id="cliente con espacios-CANARY",
                            app_name="App\x00\x1b[31mRoja\n" + long_name,
                            scope=["no es scope-CANARY", DRIVE],
                        )
                    ]
                )
            ]
        },
    )

    (item,) = section("oauth_token_events")["items"]

    assert item["client_id"] == "other"
    assert item["events"] == {"other": 1}
    assert item["scopes"] == [DRIVE, "other"]
    assert "\x00" not in item["app_name"]
    assert "\n" not in item["app_name"]
    assert len(item["app_name"]) <= 100
    assert "CANARY" not in json.dumps(item)


def test_parametros_ip_y_correos_de_los_eventos_no_se_persisten(section):
    text = json.dumps(section("oauth_token_events"))

    assert "client_type" not in text
    assert "203.0.113.7" not in text
    assert "type-CANARY" not in text


def test_los_eventos_admin_son_recuentos_por_tipo_y_nombre_sin_parametros_ni_actores(section):
    events = section("admin_events")

    assert events["status"] == "SUCCESS"
    assert events["items"] == [
        {"type": "DOMAIN_SETTINGS", "name": "ADD_DOMAIN", "count": 1},
        {"type": "USER_SETTINGS", "name": "CHANGE_PASSWORD", "count": 2},
    ]
    assert events["summary"]["activities"] == 3
    assert events["summary"]["complete"] is True
    text = json.dumps(events)
    for forbidden in ("@", "CANARY", "203.0.113.7", "parameters"):
        assert forbidden not in text


def test_un_evento_admin_con_nombre_raro_se_cuenta_como_other(api, section):
    api.set(
        ADMIN_EVENTS,
        {"items": [activity([{"type": "tipo raro-CANARY", "name": "n" * 200}])]},
    )

    events = section("admin_events")

    assert events["items"] == [{"type": "other", "name": "other", "count": 1}]


@pytest.mark.parametrize("path", [TOKEN_EVENTS, ADMIN_EVENTS])
def test_403_en_reports_da_permission_denied_incompleto_sin_datos_parciales(api, section, path):
    api.set(path, denied())
    name = "oauth_token_events" if path == TOKEN_EVENTS else "admin_events"

    events = section(name)

    assert events["status"] == "PERMISSION_DENIED"
    assert events["items"] == []
    assert events["summary"] == {"complete": False}


def test_fallo_en_la_segunda_pagina_deja_la_seccion_en_error_sin_datos_de_la_primera(api, section):
    def broken(request):
        if request.url.params.get("pageToken") is None:
            return httpx.Response(
                200,
                json={"items": fixture("token_activity")["items"], "nextPageToken": "page-1"},
            )
        return httpx.Response(500, json=google_error(500, "backendError")[1])

    api.set(TOKEN_EVENTS, broken)

    events = section("oauth_token_events")

    assert events["status"] == "ERROR"
    assert events["items"] == []
    assert events["summary"] == {"complete": False}


def test_varias_paginas_de_actividad_se_cuentan_juntas(api, section):
    items = fixture("admin_activity")["items"]
    api.set(ADMIN_EVENTS, paginated("items", items[:1], [], items[1:]))

    events = section("admin_events")

    assert events["summary"]["activities"] == 3
    assert sum(item["count"] for item in events["items"]) == 3


@pytest.mark.parametrize(
    "bad",
    [
        {"items": [{"events": []}]},
        {"items": [{"id": {"time": "no-es-fecha"}, "events": []}]},
        {"items": "no-es-lista"},
    ],
)
def test_actividad_con_forma_inesperada_da_error_de_seccion(api, section, bad):
    api.set(TOKEN_EVENTS, bad)

    events = section("oauth_token_events")

    assert events["status"] == "ERROR"
    assert events["items"] == []
