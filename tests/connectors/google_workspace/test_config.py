import pytest
import yaml

from ens_evidence.connectors.config import ConnectorConfigError, load_connector_config
from ens_evidence.connectors.google_workspace import GoogleWorkspaceConfig, GoogleWorkspaceConnector
from ens_evidence.workspace import LocalStore, WorkspaceError

from .cli_support import CONFIG, KEY, seed
from .credentials import pem_canary, private_pem
from .fake_api import ADMIN_SUBJECT, DOMAIN

SECRET_VALUE = "valor-secreto-CANARY"


@pytest.fixture
def store(tmp_path) -> LocalStore:
    return LocalStore(tmp_path / "ws")


@pytest.fixture
def seeded(store) -> LocalStore:
    return seed(store)


def write(store, document) -> None:
    store.write_text(KEY, yaml.safe_dump(document))


def load(store) -> GoogleWorkspaceConfig:
    return load_connector_config(store, "google-workspace", GoogleWorkspaceConfig)


def prepare(store, **changes) -> GoogleWorkspaceConfig:
    config = GoogleWorkspaceConnector().prepare(
        GoogleWorkspaceConfig(**{**CONFIG, **changes}), store
    )
    assert isinstance(config, GoogleWorkspaceConfig)
    return config


def test_configuracion_valida_se_carga_con_cliente_por_defecto(store):
    write(store, CONFIG)

    config = load(store)

    assert config.asset_id == "ws-identidad"
    assert config.customer == "my_customer"
    assert config.admin_subject == ADMIN_SUBJECT
    assert config.domains == [DOMAIN]


def test_un_id_de_cliente_explicito_se_acepta(store):
    write(store, {**CONFIG, "customer": "C01abc234"})

    assert load(store).customer == "C01abc234"


def test_los_dominios_se_normalizan_a_minusculas_sin_espacios(store):
    write(store, {**CONFIG, "domains": [" Taller-Norte.EXAMPLE ", "otro.example"]})

    assert load(store).domains == ["taller-norte.example", "otro.example"]


def test_el_dominio_del_admin_se_compara_sin_distinguir_mayusculas(store):
    write(store, {**CONFIG, "admin_subject": "Admin@TALLER-NORTE.example"})

    assert load(store).admin_subject == "Admin@TALLER-NORTE.example"


@pytest.mark.parametrize(
    "key", ["private_key", "client_secret", "token", "api_token", "secret", "password"]
)
def test_clave_de_credencial_en_el_yaml_se_rechaza_sin_imprimir_el_valor(store, key):
    write(store, {**CONFIG, key: SECRET_VALUE})

    with pytest.raises(ConnectorConfigError) as error:
        load(store)

    assert SECRET_VALUE not in str(error.value)
    assert KEY in str(error.value)


def test_clave_de_credencial_dentro_de_una_lista_se_rechaza(store):
    write(store, {**CONFIG, "domains": [DOMAIN, {"private_key": SECRET_VALUE}]})

    with pytest.raises(ConnectorConfigError) as error:
        load(store)

    assert SECRET_VALUE not in str(error.value)
    assert "domains[1]" in str(error.value)


def test_una_clave_privada_pegada_en_un_valor_se_rechaza_sin_imprimirla(store):
    write(store, {**CONFIG, "domains": [DOMAIN, private_pem()]})

    with pytest.raises(ConnectorConfigError) as error:
        load(store)

    assert pem_canary() not in str(error.value)


def test_fichero_ausente_indica_donde_crearlo(store):
    with pytest.raises(ConnectorConfigError, match=r"connectors/google-workspace\.yaml: no existe"):
        load(store)


@pytest.mark.parametrize(
    ("document", "fragment"),
    [
        ({k: v for k, v in CONFIG.items() if k != "asset_id"}, "asset_id"),
        ({k: v for k, v in CONFIG.items() if k != "admin_subject"}, "admin_subject"),
        ({k: v for k, v in CONFIG.items() if k != "domains"}, "domains"),
        ({**CONFIG, "asset_id": ""}, "asset_id"),
        ({**CONFIG, "customer": "otro"}, "customer"),
        ({**CONFIG, "customer": "C"}, "customer"),
        ({**CONFIG, "customer": "c01abc234"}, "customer"),
        ({**CONFIG, "customer": "C01/../x"}, "customer"),
        ({**CONFIG, "customer": "my_customer/users"}, "customer"),
        ({**CONFIG, "admin_subject": "no-es-correo"}, "admin_subject"),
        ({**CONFIG, "admin_subject": "admin@otro.example"}, "admin_subject"),
        ({**CONFIG, "domains": []}, "domains"),
        ({**CONFIG, "domains": ["no es dominio"]}, "domains"),
        ({**CONFIG, "domains": ["https://taller-norte.example"]}, "domains"),
        ({**CONFIG, "domains": ["a/b.example"]}, "domains"),
        ({**CONFIG, "token_uri": "https://evil.example/token"}, "token_uri"),
        ({**CONFIG, "scopes": ["x"]}, "scopes"),
        ({**CONFIG, "desconocida": 1}, "desconocida"),
    ],
)
def test_configuracion_incompleta_o_invalida_se_rechaza(store, document, fragment):
    write(store, document)

    with pytest.raises(ConnectorConfigError, match=fragment):
        load(store)


def test_prepare_acepta_un_activo_identity_del_inventario(seeded):
    assert prepare(seeded).asset_id == "ws-identidad"


def test_asset_id_inexistente_se_rechaza(seeded):
    with pytest.raises(WorkspaceError, match="asset_id.*nope"):
        prepare(seeded, asset_id="nope")


def test_asset_id_de_otro_tipo_se_rechaza(seeded):
    with pytest.raises(WorkspaceError, match="identity.*srv-app"):
        prepare(seeded, asset_id="srv-app")


def test_sin_inventario_prepare_lo_indica(store):
    with pytest.raises(WorkspaceError, match="No se puede leer el inventario"):
        prepare(store)
