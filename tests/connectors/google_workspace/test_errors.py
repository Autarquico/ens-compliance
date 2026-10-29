import httpx
import pytest

from ens_evidence.connectors.google_workspace.client import EDITION_REASONS

from .fake_api import TOKEN_EVENTS, USERS, google_error

EDITION_REASON = sorted(EDITION_REASONS)[0]
QUOTA = "HTTP {}: límite de peticiones alcanzado"


def reason_of(section: dict) -> str:
    return section["reason"]


@pytest.mark.parametrize(
    "reply",
    [
        google_error(429, "rateLimitExceeded", status="RESOURCE_EXHAUSTED"),
        google_error(429, "quotaExceeded"),
        (429, "<html>proxy-CANARY</html>"),
    ],
)
def test_429_deja_la_seccion_en_error_nunca_en_permission_denied(api, section, reply):
    api.set(USERS, reply)

    users = section("users")

    assert users["status"] == "ERROR"
    assert reason_of(users) == QUOTA.format(429)


@pytest.mark.parametrize(
    "reason",
    ["rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded", "dailyLimitExceeded"],
)
def test_403_por_cuota_es_error_porque_la_cuota_se_evalua_antes_que_el_permiso(
    api, section, reason
):
    api.set(USERS, google_error(403, reason))

    users = section("users")

    assert users["status"] == "ERROR"
    assert reason_of(users) == QUOTA.format(403)


def test_403_con_motivos_de_cuota_y_de_permiso_a_la_vez_prevalece_la_cuota(api, section):
    api.set(
        USERS,
        (
            403,
            {
                "error": {
                    "errors": [
                        {"reason": "insufficientPermissions"},
                        {"reason": "rateLimitExceeded"},
                    ]
                }
            },
        ),
    )

    assert section("users")["status"] == "ERROR"


@pytest.mark.parametrize("code", [500, 502, 503, 504])
def test_5xx_deja_la_seccion_en_error_con_motivo_fijo(api, section, code):
    api.set(USERS, (code, "<html>boom-CANARY</html>"))

    users = section("users")

    assert users["status"] == "ERROR"
    assert reason_of(users) == f"HTTP {code}: error de la API"


@pytest.mark.parametrize("code", [400, 404])
def test_4xx_inesperado_es_error_de_seccion_y_no_permiso(api, section, code):
    api.set(USERS, google_error(code, "badRequest"))

    users = section("users")

    assert users["status"] == "ERROR"
    assert reason_of(users) == f"HTTP {code}: error de la API"


@pytest.mark.parametrize("reason", ["insufficientPermissions", "forbidden", "accessNotConfigured"])
def test_403_normal_es_permission_denied(api, section, reason):
    api.set(USERS, google_error(403, reason))

    users = section("users")

    assert users["status"] == "PERMISSION_DENIED"
    assert reason_of(users) == "HTTP 403: permiso insuficiente"


def test_403_sin_cuerpo_interpretable_es_permission_denied(api, section):
    api.set(USERS, (403, "<html>forbidden</html>"))

    assert section("users")["status"] == "PERMISSION_DENIED"


def test_not_supported_solo_con_el_error_de_edicion_explicito(api, section):
    api.set(TOKEN_EVENTS, google_error(403, EDITION_REASON))

    events = section("oauth_token_events")

    assert events["status"] == "NOT_SUPPORTED"
    assert reason_of(events) == "HTTP 403: función no disponible en la edición"


@pytest.mark.parametrize(
    "reply",
    [
        google_error(403, "forbidden", "Esta función no está disponible en su edición"),
        google_error(400, EDITION_REASON),
        google_error(404, "notFound", "Not supported in this edition"),
    ],
)
def test_sin_el_error_explicito_de_edicion_nunca_hay_not_supported(api, section, reply):
    api.set(TOKEN_EVENTS, reply)

    assert section("oauth_token_events")["status"] in {"PERMISSION_DENIED", "ERROR"}


def test_un_error_de_red_en_un_endpoint_es_error_de_seccion_con_motivo_fijo(api, section):
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout-CANARY", request=request)

    api.set(USERS, boom)

    users = section("users")

    assert users["status"] == "ERROR"
    assert reason_of(users) == "fallo de red: ReadTimeout"


def test_un_error_de_red_al_pedir_el_token_da_error_global(api, collect):
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connect-CANARY", request=request)

    api.set("/token", boom)

    result = collect()

    assert result.collection_status == "ERROR"
    assert result.data["sections"] == {}
    assert "fallo de red al pedir el token: ConnectError" in result.data["error"]
    assert "CANARY" not in str(result.data)


@pytest.mark.parametrize("body", [b"no es json", b"[1, 2]", b'"texto"', b"null"])
def test_respuesta_200_mal_formada_es_error_de_seccion(api, section, body):
    api.set(USERS, (200, body))

    users = section("users")

    assert users["status"] == "ERROR"
    assert reason_of(users) == "HTTP 200: respuesta mal formada"


def test_el_cuerpo_de_un_error_nunca_llega_al_resultado(api, collect):
    api.set(USERS, google_error(403, "forbidden", "denied-CANARY"))
    api.set(TOKEN_EVENTS, (502, "<html>proxy-CANARY</html>"))

    result = collect()

    assert "CANARY" not in result.model_dump_json()
