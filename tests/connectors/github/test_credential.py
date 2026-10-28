import pytest

from ens_evidence.connectors.github import GitHubConnector
from ens_evidence.connectors.github.connector import TOKEN_ENV
from ens_evidence.models.enums import CollectionStatus

from .fake_api import FACTURAS, ORG, REPO, TOKEN, fixture, message


def assert_global_error(result, fragment: str | None = None) -> None:
    assert result.collection_status is CollectionStatus.ERROR
    assert result.data["error"]
    if fragment:
        assert fragment in result.data["error"]


def test_token_revocado_al_inicio_es_error_global_sin_pedir_nada_mas(api, collect):
    api.set("/user", (401, message("Bad credentials")))

    result = collect()

    assert_global_error(result, "HTTP 401")
    assert result.data["sections"] == {}
    assert api.paths() == ["/user"]


def test_token_revocado_al_final_invalida_toda_la_recogida(api, collect):
    api.sequence("/user", [fixture("user"), (401, message("Bad credentials"))])

    result = collect()

    assert_global_error(result, "HTTP 401")
    assert api.paths().count("/user") == 2


def test_token_revocado_a_mitad_invalida_toda_la_recogida(api, collect):
    api.set(rf"{REPO}/keys", (401, message("Bad credentials")))

    result = collect()

    assert_global_error(result, "HTTP 401")
    assert "/user" not in api.paths()[1:]


@pytest.mark.parametrize(
    "reply",
    [(403, message("Forbidden")), (404, message("Not Found")), (500, message("boom")), (200, [])],
)
def test_token_que_no_se_puede_verificar_es_error_global(api, collect, reply):
    api.set("/user", reply)

    result = collect()

    assert_global_error(result, "el token no se puede verificar")
    assert result.data["sections"] == {}


def test_limite_de_peticiones_al_verificar_es_error_global_no_permiso_denegado(api, collect):
    api.set("/user", (403, message("rate limit"), {"x-ratelimit-remaining": "0"}))

    result = collect()

    assert result.collection_status is CollectionStatus.ERROR
    assert "límite de peticiones" in result.data["error"]


@pytest.mark.parametrize("env", [{}, {TOKEN_ENV: "  "}])
def test_sin_token_es_error_global_sin_llamar_a_la_red(api, config, env):
    connector = GitHubConnector(transport=api.transport, env=env)

    result = connector.collect(config)

    assert_global_error(result, TOKEN_ENV)
    assert api.calls == []


def test_token_con_caracteres_no_validos_es_error_y_no_se_muestra(api, config):
    secreto = "tókén-CANARY"
    connector = GitHubConnector(transport=api.transport, env={TOKEN_ENV: secreto})

    result = connector.collect(config)

    assert_global_error(result, TOKEN_ENV)
    assert secreto not in result.model_dump_json()
    assert api.calls == []


def test_el_token_viaja_solo_en_la_cabecera_de_autorizacion(api, collect):
    collect()

    assert {call.headers["authorization"] for call in api.calls} == {f"Bearer {TOKEN}"}
    assert all(TOKEN not in str(call.url) for call in api.calls)


def test_con_token_valido_la_recogida_es_success_y_pide_el_inventario_de_la_organizacion(
    api, collect
):
    result = collect()

    assert result.collection_status is CollectionStatus.SUCCESS
    assert result.data["error"] is None
    assert result.asset_id == "gh-taller"
    assert f"/orgs/{ORG}/repos" in api.paths()
    assert f"{FACTURAS}/keys" in api.paths()
