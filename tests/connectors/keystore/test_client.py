"""La lista blanca de (método, ruta) y el trato del token."""

import logging

import httpx
import pytest

from ens_evidence.connectors.keystore.client import KeystoreClient, KeystoreError, NotFound
from ens_evidence.connectors.keystore.connector import TOKEN_ENV
from ens_evidence.models.enums import CollectionStatus

from .fake_openbao import TOKEN, FakeOpenBao


@pytest.fixture
def client(api: FakeOpenBao):
    with KeystoreClient("http://openbao:8200", TOKEN, transport=api.transport) as c:
        yield c


REJECTED = [
    ("GET", "kv/data/secreto"),
    ("GET", "kv/subkeys/secreto"),
    ("GET", "kv/detailed-metadata/"),
    ("LIST", "kv/detailed-metadata/"),
    ("SCAN", "kv/metadata/"),
    ("DELETE", "kv/metadata/app"),
    ("PUT", "kv/metadata/app"),
    ("POST", "kv/metadata/app"),
    ("POST", "kv/data/secreto"),
    ("GET", "kv/metadata/../data/x"),
    ("LIST", "kv/metadata/a/../../"),
    ("GET", "kv/metadata/.."),
    ("GET", "transit/keys/.."),
    ("GET", "kv/metadata/?list=true"),
    ("GET", "transit/export/encryption-key/sim"),
    ("POST", "transit/decrypt/sim"),
    ("POST", "transit/keys/sim/rotate"),
    ("GET", "transit/keys/sim/extra"),
    ("GET", "sys/policies/acl"),
    ("LIST", "sys/policies/acl"),
    ("GET", "sys/policies/acl/root"),
    ("PUT", "sys/policies/acl/ens-app-metadata"),
    ("POST", "sys/audit/archivo"),
    ("PUT", "sys/audit/archivo"),
    ("POST", "sys/init"),
    ("POST", "sys/unseal"),
    ("POST", "sys/seal"),
    ("GET", "sys/raw/x"),
    ("POST", "sys/generate-root-token/attempt"),
    ("POST", "auth/token/create"),
    ("POST", "auth/token/revoke-accessor"),
    ("POST", "auth/token/revoke-self"),
    ("POST", "auth/token/renew-accessor"),
    ("GET", "auth/token/accessors"),
    ("POST", "auth/token/accessors"),
    ("GET", "auth/token/lookup-accessor"),
    ("POST", "auth/token/lookup-self"),
    ("GET", "auth/token/renew-self"),
    ("GET", "sys/capabilities-self"),
    ("POST", "sys/seal-status"),
    ("GET", "sys/storage/raft/snapshot"),
    ("GET", "http://evil.example/v1/sys/mounts"),
    ("GET", "/sys/mounts"),
    ("GET", "sys/mounts/"),
]


@pytest.mark.parametrize(("method", "path"), REJECTED)
def test_rechaza_cualquier_otra_ruta_o_metodo_sin_enviar_nada(client, api, method, path):
    with pytest.raises(KeystoreError, match="ruta no permitida"):
        client._send(method, path)

    assert api.requests == []


ALLOWED = [
    ("GET", "sys/seal-status"),
    ("GET", "sys/audit"),
    ("GET", "sys/mounts"),
    ("GET", "sys/policies/acl/ens-app-metadata"),
    ("LIST", "kv/metadata/"),
    ("LIST", "kv/metadata/dir/"),
    ("GET", "kv/metadata/dir/db"),
    ("LIST", "transit/keys/"),
    ("GET", "transit/keys/sim"),
    ("LIST", "auth/token/accessors"),
    ("POST", "auth/token/lookup-accessor"),
    ("GET", "auth/token/lookup-self"),
    ("POST", "auth/token/renew-self"),
    ("POST", "sys/capabilities-self"),
]


@pytest.mark.parametrize(("method", "path"), ALLOWED)
def test_permite_exactamente_las_rutas_de_la_lista_blanca(client, api, method, path):
    api.set("POST", "auth/token/renew-self", (200, {}))
    api.set("POST", "auth/token/lookup-accessor", (200, {"data": {}}))
    api.set("LIST", "kv/metadata/dir/", (200, {"data": {"keys": []}}))

    client._send(method, path, {} if method == "POST" else None)

    assert api.requests == [(method, path)]


def test_un_nombre_con_puntos_o_separadores_no_sale_de_su_montaje(client, api):
    from ens_evidence.connectors.keystore.client import segment

    assert segment("a b?c#") == "a%20b%3Fc%23"
    assert segment("a/b") == "a%2Fb"
    for name in ("", ".", ".."):
        with pytest.raises(KeystoreError):
            segment(name)


def test_el_404_es_not_found_y_el_403_dice_que_no_hay_permiso(client, api):
    api.set("GET", "sys/audit", (403, {"errors": ["permission denied"]}))

    api.drop("GET", "sys/policies/acl/ens-app-metadata")
    with pytest.raises(NotFound):
        client.get("sys/policies/acl/ens-app-metadata")
    with pytest.raises(KeystoreError, match="no tiene permiso") as denied:
        client.get("sys/audit")
    assert denied.value.status is CollectionStatus.PERMISSION_DENIED


def test_el_token_no_aparece_en_repr_errores_ni_logs(api, collect, caplog):
    caplog.set_level(logging.DEBUG)
    api.set("GET", "sys/audit", (500, {"errors": [f"fallo con {TOKEN}"]}))
    with KeystoreClient("http://openbao:8200", TOKEN, transport=api.transport) as c:
        assert TOKEN not in repr(c)
        with pytest.raises(KeystoreError) as error:
            c.get("sys/audit")
    assert TOKEN not in error.value.message and "[redactado]" in error.value.message

    result = collect()

    assert TOKEN not in caplog.text
    assert TOKEN not in result.model_dump_json()
    assert set(api.tokens) == {TOKEN}


def test_sin_token_no_se_envia_cabecera(api):
    with KeystoreClient("http://openbao:8200", None, transport=api.transport) as c:
        c.get("sys/seal-status")

    assert api.tokens == [None]


def test_un_fallo_de_red_se_reduce_al_nombre_de_la_excepcion():
    def boom(request):
        raise httpx.ConnectError(f"no conecta con {TOKEN}")

    with KeystoreClient("http://openbao:8200", TOKEN, transport=httpx.MockTransport(boom)) as c:
        with pytest.raises(KeystoreError) as error:
            c.get("sys/seal-status")

    assert error.value.message == "fallo de red: ConnectError"
    assert TOKEN not in str(error.value)


def test_no_sigue_redirecciones(api):
    api.set("GET", "sys/mounts", lambda r: httpx.Response(307, headers={"location": "http://x/"}))
    with KeystoreClient("http://openbao:8200", TOKEN, transport=api.transport) as c:
        with pytest.raises(KeystoreError, match="HTTP 307"):
            c.get("sys/mounts")
    assert api.count("GET", "sys/mounts") == 1


def test_el_nombre_de_la_variable_de_entorno():
    assert TOKEN_ENV == "ENS_KEYSTORE_TOKEN"
