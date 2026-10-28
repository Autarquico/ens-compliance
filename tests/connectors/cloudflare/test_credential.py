import pytest

from ens_evidence.connectors.cloudflare import CloudflareConnector
from ens_evidence.models.enums import CollectionStatus

from .fake_api import ACCOUNT, NOW, TOKEN, FakeApi, envelope, failure

ACCOUNT_VERIFY = f"/accounts/{ACCOUNT}/tokens/verify"
USER_VERIFY = "/user/tokens/verify"
SECTIONS = {
    "zones",
    "dns",
    "tls",
    "waf",
    "access_applications",
    "access_policies",
    "tunnels",
    "audit_logs",
}


def test_con_credencial_valida_el_resultado_es_success_con_todas_las_secciones(collect):
    result = collect()

    assert result.collection_status is CollectionStatus.SUCCESS
    assert result.data["error"] is None
    assert set(result.data["sections"]) == SECTIONS
    assert {s["status"] for s in result.data["sections"].values()} == {"SUCCESS"}


def test_token_revocado_da_error_global_sin_leer_ninguna_seccion(api, collect):
    api.set(r"/.*/tokens/verify", (401, failure(1000, "Invalid API Token")))

    result = collect()

    assert result.collection_status is CollectionStatus.ERROR
    assert "401" in result.data["error"]
    assert result.data["sections"] == {}
    assert api.paths() == [ACCOUNT_VERIFY]


@pytest.mark.parametrize("state", ["expired", "disabled", None])
def test_token_que_no_esta_activo_da_error_global(api, collect, state):
    api.set(r"/.*/tokens/verify", envelope({"id": "t", "status": state}))

    result = collect()

    assert result.collection_status is CollectionStatus.ERROR
    assert "no está activo" in result.data["error"]
    assert result.data["sections"] == {}


def test_verify_con_permiso_denegado_es_error_global_no_permission_denied(api, collect):
    api.set(r"/.*/tokens/verify", (403, failure(10000, "Authentication error")))

    result = collect()

    assert result.collection_status is CollectionStatus.ERROR
    assert result.data["sections"] == {}


def test_verify_con_fallo_del_servidor_es_error_global(api, collect):
    api.set(r"/.*/tokens/verify", (500, failure(1, "boom")))

    result = collect()

    assert result.collection_status is CollectionStatus.ERROR
    assert api.paths() == [ACCOUNT_VERIFY]


def test_sin_variable_de_entorno_da_error_sin_llamar_a_la_red(api, config):
    connector = CloudflareConnector(transport=api.transport, clock=lambda: NOW, env={})

    result = connector.collect(config)

    assert result.collection_status is CollectionStatus.ERROR
    assert "ENS_CLOUDFLARE_API_TOKEN" in result.data["error"]
    assert api.calls == []


def test_token_de_cuenta_se_verifica_en_el_endpoint_de_cuenta(api, collect):
    collect(token_kind="account")

    assert ACCOUNT_VERIFY in api.paths()
    assert USER_VERIFY not in api.paths()


def test_token_de_usuario_se_verifica_en_el_endpoint_de_usuario(api, collect):
    collect(token_kind="user")

    assert USER_VERIFY in api.paths()
    assert ACCOUNT_VERIFY not in api.paths()


@pytest.mark.parametrize(
    ("kind", "rejected", "other"),
    [("account", ACCOUNT_VERIFY, USER_VERIFY), ("user", USER_VERIFY, ACCOUNT_VERIFY)],
)
def test_verify_rechazado_no_cae_al_otro_endpoint(api, collect, kind, rejected, other):
    api.set(rejected, (401, failure(1000, "Invalid API Token")))

    result = collect(token_kind=kind)

    assert result.collection_status is CollectionStatus.ERROR
    assert api.paths() == [rejected]
    assert other not in api.paths()


def test_la_credencial_se_envia_como_bearer_solo_en_la_cabecera(api, collect):
    collect()

    assert {call.headers["Authorization"] for call in api.calls} == {f"Bearer {TOKEN}"}
    assert all(TOKEN not in str(call.url) for call in api.calls)


def test_revocacion_a_mitad_invalida_el_conjunto_pero_conserva_lo_recogido(api: FakeApi, collect):
    api.set(
        r"/.*/tokens/verify", [envelope({"id": "t", "status": "active"}), (401, failure(1, "x"))]
    )

    result = collect()

    assert result.collection_status is CollectionStatus.ERROR
    assert "401" in result.data["error"]
    assert set(result.data["sections"]) == SECTIONS
    assert api.paths().count(ACCOUNT_VERIFY) == 2
    assert api.paths()[-1] == ACCOUNT_VERIFY


def test_token_que_deja_de_estar_activo_a_mitad_da_error_global(api: FakeApi, collect):
    api.set(
        r"/.*/tokens/verify",
        [envelope({"id": "t", "status": "active"}), envelope({"id": "t", "status": "disabled"})],
    )

    result = collect()

    assert result.collection_status is CollectionStatus.ERROR
    assert "no está activo" in result.data["error"]
