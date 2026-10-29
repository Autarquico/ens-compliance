from datetime import timedelta
from urllib.parse import parse_qs

import pytest

from ens_evidence.connectors.google_workspace.auth import ServiceAccountKey
from ens_evidence.connectors.google_workspace.client import WorkspaceClient

from .credentials import CLIENT_EMAIL, EVIL, decode_assertion, key_json
from .fake_api import ACCESS_TOKEN, ADMIN_SUBJECT, CUSTOMER, NOW, token_response

READ_ONLY_SCOPES = {
    "https://www.googleapis.com/auth/admin.directory.user.readonly",
    "https://www.googleapis.com/auth/admin.directory.rolemanagement.readonly",
    "https://www.googleapis.com/auth/admin.reports.audit.readonly",
    "https://www.googleapis.com/auth/admin.directory.customer.readonly",
}


def first_claims(api) -> tuple[dict, dict]:
    return decode_assertion(api.assertions[0])


def test_la_asercion_va_firmada_con_rs256_y_la_firma_verifica_con_la_clave_publica(api, collect):
    collect()

    header, _ = first_claims(api)

    assert header == {"alg": "RS256", "typ": "JWT"}


def test_la_asercion_identifica_a_la_cuenta_de_servicio_y_suplanta_al_admin_configurado(
    api, collect
):
    collect()

    _, claims = first_claims(api)

    assert claims["iss"] == CLIENT_EMAIL
    assert claims["sub"] == ADMIN_SUBJECT


def test_la_audiencia_es_el_endpoint_de_token_fijo(api, collect):
    collect()

    assert first_claims(api)[1]["aud"] == "https://oauth2.googleapis.com/token"


def test_los_scopes_son_exactamente_los_de_solo_lectura(api, collect):
    collect()

    scopes = first_claims(api)[1]["scope"].split(" ")

    assert len(scopes) == len(set(scopes))
    assert set(scopes) == READ_ONLY_SCOPES
    assert not any("security" in scope or not scope.endswith(".readonly") for scope in scopes)


def test_la_asercion_vive_como_mucho_una_hora(api, collect):
    collect()

    claims = first_claims(api)[1]

    assert claims["iat"] == int(NOW.timestamp())
    assert 0 < claims["exp"] - claims["iat"] <= 3600


def test_el_token_se_pide_por_post_con_grant_type_jwt_bearer_y_solo_esos_campos(api, collect):
    collect()

    (request,) = api.token_requests()

    assert request.method == "POST"
    assert str(request.url) == "https://oauth2.googleapis.com/token"
    assert request.headers["content-type"] == "application/x-www-form-urlencoded"
    form = parse_qs(request.content.decode())
    assert set(form) == {"grant_type", "assertion"}
    assert form["grant_type"] == ["urn:ietf:params:oauth:grant-type:jwt-bearer"]


def test_las_urls_del_fichero_de_credenciales_se_ignoran(api, collect):
    collect()

    assert api.hosts() == {"oauth2.googleapis.com", "admin.googleapis.com"}
    assert EVIL not in "".join(str(request.url) for request in api.calls)


def test_el_token_de_acceso_viaja_solo_como_bearer_a_la_api_y_se_reutiliza(api, collect):
    collect()

    assert len(api.token_requests()) == 1
    assert {r.headers["Authorization"] for r in api.api_calls()} == {f"Bearer {ACCESS_TOKEN}"}


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self):
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def client(api, clock):
    http = WorkspaceClient(
        ServiceAccountKey(key_json()), ADMIN_SUBJECT, transport=api.transport, clock=clock
    )
    with http as opened:
        yield opened


def test_el_token_se_renueva_al_caducar(api, client, clock):
    client.get(CUSTOMER)
    clock.now = NOW + timedelta(seconds=3600)

    client.get(CUSTOMER)

    assert len(api.token_requests()) == 2
    renewed = decode_assertion(api.assertions[1])[1]
    assert renewed["iat"] == int((NOW + timedelta(seconds=3600)).timestamp())


def test_el_token_no_se_renueva_mientras_es_valido(api, client, clock):
    client.get(CUSTOMER)
    clock.now = NOW + timedelta(seconds=600)

    client.get(CUSTOMER)

    assert len(api.token_requests()) == 1


def test_la_vida_del_token_nunca_supera_una_hora_aunque_el_servidor_ofrezca_mas(api, client, clock):
    api.set("/token", token_response(expires_in=86400))
    client.get(CUSTOMER)
    clock.now = NOW + timedelta(seconds=3600)

    client.get(CUSTOMER)

    assert len(api.token_requests()) == 2


@pytest.mark.parametrize("lifetime", [0, -5, "3600", None, True])
def test_una_vida_de_token_mal_formada_se_trata_como_el_maximo_de_una_hora(
    api, client, clock, lifetime
):
    api.set("/token", token_response(expires_in=lifetime))
    client.get(CUSTOMER)
    clock.now = NOW + timedelta(seconds=3600)

    client.get(CUSTOMER)

    assert len(api.token_requests()) == 2


def test_una_redireccion_del_endpoint_de_token_no_se_sigue(api, collect):
    api.set("/token", (302, b"", {"Location": f"{EVIL}/token"}))

    result = collect()

    assert result.data["error"] is not None
    assert api.hosts() == {"oauth2.googleapis.com"}


def test_una_redireccion_de_la_api_no_se_sigue(api, section):
    api.set("/admin/directory/v1/users", (302, b"", {"Location": f"{EVIL}/users"}))

    users = section("users")

    assert users["status"] == "ERROR"
    assert EVIL.removeprefix("https://") not in api.hosts()
