import pytest
import yaml

from ens_evidence.connectors.cloudflare import CloudflareConfig
from ens_evidence.connectors.config import ConnectorConfigError, load_connector_config
from ens_evidence.workspace import LocalStore

from .fake_api import ACCOUNT

KEY = "connectors/cloudflare.yaml"
SECRET_VALUE = "valor-secreto-CANARY"
BASE = {"asset_id": "cf-taller", "account_id": ACCOUNT}


@pytest.fixture
def store(tmp_path) -> LocalStore:
    return LocalStore(tmp_path / "ws")


def write(store, document) -> None:
    store.write_text(KEY, yaml.safe_dump(document))


def load(store) -> CloudflareConfig:
    return load_connector_config(store, "cloudflare", CloudflareConfig)


def test_configuracion_valida_se_carga_con_token_account_por_defecto(store):
    write(store, BASE)

    config = load(store)

    assert config.token_kind == "account"
    assert config.public_routes == []


def test_token_kind_user_es_valido_aunque_su_nombre_contenga_token(store):
    write(store, {**BASE, "token_kind": "user"})

    assert load(store).token_kind == "user"


def test_rutas_publicas_declaradas_con_motivo(store):
    route = {"hostname": "admin.taller-norte.example", "path": "/agent/*", "reason": "API agente"}
    write(store, {**BASE, "public_routes": [route]})

    assert load(store).public_routes[0].path == "/agent/*"


@pytest.mark.parametrize("key", ["api_token", "apiToken", "secret", "password", "credentials"])
def test_clave_de_credencial_en_el_yaml_se_rechaza_sin_imprimir_el_valor(store, key):
    write(store, {**BASE, key: SECRET_VALUE})

    with pytest.raises(ConnectorConfigError) as error:
        load(store)

    assert SECRET_VALUE not in str(error.value)
    assert KEY in str(error.value)


def test_clave_de_credencial_dentro_de_una_lista_se_rechaza(store):
    route = {"hostname": "a.example", "reason": "x", "api_token": SECRET_VALUE}
    write(store, {**BASE, "public_routes": [route]})

    with pytest.raises(ConnectorConfigError) as error:
        load(store)

    assert SECRET_VALUE not in str(error.value)
    assert "public_routes[0]" in str(error.value)


def test_valor_con_forma_de_secreto_se_rechaza_aunque_la_clave_sea_inocua(store):
    write(store, {**BASE, "account_id": "ghp_" + "A1b2C3d4E5" * 4})

    with pytest.raises(ConnectorConfigError, match="secretos"):
        load(store)


def test_fichero_ausente_indica_donde_crearlo(store):
    with pytest.raises(ConnectorConfigError, match=r"connectors/cloudflare\.yaml: no existe"):
        load(store)


def test_yaml_que_no_es_un_mapa_se_rechaza(store):
    store.write_text(KEY, "- a\n- b\n")

    with pytest.raises(ConnectorConfigError, match="mapa"):
        load(store)


def test_yaml_invalido_se_rechaza(store):
    store.write_text(KEY, "asset_id: [\n")

    with pytest.raises(ConnectorConfigError, match="YAML"):
        load(store)


@pytest.mark.parametrize(
    ("document", "fragment"),
    [
        ({"account_id": ACCOUNT}, "asset_id"),
        ({"asset_id": "cf-taller"}, "account_id"),
        ({**BASE, "token_kind": "global"}, "token_kind"),
        ({**BASE, "desconocida": 1}, "desconocida"),
        (
            {**BASE, "public_routes": [{"hostname": "a.example", "reason": "  "}]},
            "motivo",
        ),
        ({**BASE, "public_routes": [{"hostname": "a.example"}]}, "reason"),
    ],
)
def test_configuracion_incompleta_o_con_claves_desconocidas_se_rechaza(store, document, fragment):
    write(store, document)

    with pytest.raises(ConnectorConfigError, match=fragment):
        load(store)
