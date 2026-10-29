import subprocess
import sys
import textwrap
import traceback

import pytest

from ens_evidence.connectors.google_workspace import KEY_ENV, GoogleWorkspaceConnector
from ens_evidence.connectors.google_workspace.auth import (
    CredentialRejected,
    ServiceAccountKey,
    TokenProvider,
)
from ens_evidence.connectors.google_workspace.client import WorkspaceClient

from .credentials import key_json, pem_canary, private_pem
from .fake_api import NOW

EC_PEM = (
    "-----BEGIN PRIVATE KEY-----\n"
    "MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQgevZzL1gdAFr88hb2\n"
    "OF/2NxApJCzGCEDdfSp6VQO30hyhRANCAAQRWz+jn65BtOMvdyHKcvjBeBSDZH2r\n"
    "-----END PRIVATE KEY-----\n"
)


def collect_with(api, config, raw_key: str | None):
    env = {} if raw_key is None else {KEY_ENV: raw_key}
    connector = GoogleWorkspaceConnector(transport=api.transport, clock=lambda: NOW, env=env)
    return connector.collect(config)


@pytest.mark.parametrize("raw_key", [None, "", "   \n"])
def test_sin_clave_en_el_entorno_da_error_global_sin_tocar_la_red(api, config, raw_key):
    result = collect_with(api, config, raw_key)

    assert result.collection_status == "ERROR"
    assert result.data["error"] == f"{KEY_ENV} no está definida"
    assert api.calls == []


@pytest.mark.parametrize(
    ("raw_key", "fragment"),
    [
        ("esto-CANARY no es json", "no es JSON"),
        ("[1, 2]", "no tiene client_email"),
        (key_json(client_email=None), "no tiene client_email"),
        (key_json(client_email="no-es-un-correo"), "no tiene client_email"),
        (key_json(private_key=None), "no tiene private_key"),
        (key_json(private_key="sin-marcador-CANARY"), "no tiene private_key"),
        (key_json(private_key=123), "no tiene private_key"),
    ],
)
def test_clave_incompleta_da_error_global_con_motivo_fijo_sin_tocar_la_red(
    api, config, raw_key, fragment
):
    result = collect_with(api, config, raw_key)

    assert result.collection_status == "ERROR"
    assert fragment in result.data["error"]
    assert "CANARY" not in result.data["error"]
    assert api.calls == []


@pytest.mark.parametrize(
    "private_key",
    [
        "-----BEGIN PRIVATE KEY-----\nmalformada-CANARY-0123456789\n-----END PRIVATE KEY-----\n",
        EC_PEM,
    ],
    ids=["pem-malformado", "clave-no-rsa"],
)
def test_clave_privada_invalida_da_error_global_y_nunca_filtra_su_contenido(
    api, config, private_key
):
    result = collect_with(api, config, key_json(private_key=private_key))

    assert result.collection_status == "ERROR"
    assert result.data["error"] == "la clave privada de la cuenta de servicio no es válida"
    assert api.calls == []


def test_la_excepcion_de_clave_malformada_no_contiene_la_clave_ni_encadena_su_causa():
    broken = (
        "-----BEGIN PRIVATE KEY-----\nmalformada-CANARY-0123456789\n-----END PRIVATE KEY-----\n"
    )
    key = ServiceAccountKey(key_json(private_key=broken))

    with pytest.raises(CredentialRejected) as error:
        key.sign(b"mensaje")

    text = "".join(traceback.format_exception(error.value))
    assert "malformada-CANARY" not in text
    assert error.value.__cause__ is None
    assert error.value.__suppress_context__


def test_un_fichero_de_credenciales_que_no_es_json_no_se_imprime_en_la_excepcion():
    raw = "{no-json-CANARY"

    with pytest.raises(CredentialRejected) as error:
        ServiceAccountKey(raw)

    assert "CANARY" not in str(error.value)
    assert error.value.__cause__ is None


def test_la_clave_y_el_token_no_aparecen_en_repr(api, config):
    key = ServiceAccountKey(key_json())
    client = WorkspaceClient(key, "admin@taller-norte.example", clock=lambda: NOW)
    provider = TokenProvider(key, "admin@taller-norte.example", client._http, lambda: NOW)

    for obj in (key, client, provider):
        assert pem_canary() not in repr(obj)
        assert private_pem() not in repr(obj)
    assert not hasattr(key, "__dict__")


def test_sin_el_extra_workspace_la_recogida_da_error_global_con_instruccion_clara(
    api, collect, monkeypatch
):
    monkeypatch.setitem(sys.modules, "cryptography.hazmat.primitives", None)

    result = collect()

    assert result.collection_status == "ERROR"
    assert "ens-evidence[workspace]" in result.data["error"]
    assert api.calls == []


def test_el_conector_se_importa_y_se_registra_sin_cryptography_instalado():
    script = textwrap.dedent(
        """
        import sys

        class Blocker:
            def find_spec(self, name, path=None, target=None):
                if name.split(".")[0] == "cryptography":
                    raise ImportError(name)

        sys.meta_path.insert(0, Blocker())
        from ens_evidence.connectors import ConnectorRegistry

        registry = ConnectorRegistry()
        registry.load_entry_points()
        assert "google-workspace" in registry.names()
        """
    )

    done = subprocess.run([sys.executable, "-I", "-c", script], capture_output=True, text=True)

    assert done.returncode == 0, done.stderr
