import pytest
import yaml

from ens_evidence.connectors.config import ConnectorConfigError, load_connector_config
from ens_evidence.connectors.github import GitHubConfig, GitHubConnector, GitHubRun
from ens_evidence.workspace import LocalStore, WorkspaceError

from .cli_support import CONFIG, KEY, seed
from .fake_api import ORG

SECRET_VALUE = "valor-secreto-CANARY"


@pytest.fixture
def store(tmp_path) -> LocalStore:
    return LocalStore(tmp_path / "ws")


@pytest.fixture
def seeded(store) -> LocalStore:
    return seed(store)


def write(store, document) -> None:
    store.write_text(KEY, yaml.safe_dump(document))


def load(store) -> GitHubConfig:
    return load_connector_config(store, "github", GitHubConfig)


def set_repos(store, repos) -> None:
    assets = yaml.safe_load(store.read_text("scope/assets.yaml"))
    next(a for a in assets["assets"] if a["id"] == "gh-taller")["repos"] = repos
    store.write_text("scope/assets.yaml", yaml.safe_dump(assets))


def prepare(store, **changes) -> GitHubRun:
    run = GitHubConnector().prepare(GitHubConfig(**{**CONFIG, **changes}), store)
    assert isinstance(run, GitHubRun)
    return run


def test_configuracion_valida_se_carga(store):
    write(store, CONFIG)

    config = load(store)

    assert config.asset_id == "gh-taller"
    assert config.organization == ORG


@pytest.mark.parametrize("key", ["token", "api_token", "apiToken", "secret", "password"])
def test_clave_de_credencial_en_el_yaml_se_rechaza_sin_imprimir_el_valor(store, key):
    write(store, {**CONFIG, key: SECRET_VALUE})

    with pytest.raises(ConnectorConfigError) as error:
        load(store)

    assert SECRET_VALUE not in str(error.value)
    assert KEY in str(error.value)


def test_clave_de_credencial_dentro_de_una_lista_se_rechaza(store):
    write(store, {**CONFIG, "repos": [{"name": "facturas", "token": SECRET_VALUE}]})

    with pytest.raises(ConnectorConfigError) as error:
        load(store)

    assert SECRET_VALUE not in str(error.value)
    assert "repos[0]" in str(error.value)


def test_valor_con_forma_de_token_de_github_se_rechaza_aunque_la_clave_sea_inocua(store):
    write(store, {**CONFIG, "organization": "ghp_" + "A1b2C3d4E5" * 4})

    with pytest.raises(ConnectorConfigError, match="secretos"):
        load(store)


def test_fichero_ausente_indica_donde_crearlo(store):
    with pytest.raises(ConnectorConfigError, match=r"connectors/github\.yaml: no existe"):
        load(store)


@pytest.mark.parametrize(
    ("document", "fragment"),
    [
        ({"organization": ORG}, "asset_id"),
        ({"asset_id": "gh-taller"}, "organization"),
        ({**CONFIG, "asset_id": ""}, "asset_id"),
        ({**CONFIG, "organization": "-mala"}, "organization"),
        ({**CONFIG, "organization": "../otra"}, "organization"),
        ({**CONFIG, "organization": "a/b"}, "organization"),
        ({**CONFIG, "organization": "x" * 40}, "organization"),
        ({**CONFIG, "api_url": "https://ghe.example"}, "api_url"),
        ({**CONFIG, "desconocida": 1}, "desconocida"),
    ],
)
def test_configuracion_incompleta_o_con_claves_desconocidas_se_rechaza(store, document, fragment):
    write(store, document)

    with pytest.raises(ConnectorConfigError, match=fragment):
        load(store)


def test_los_repos_esperados_salen_del_activo_code_hosting(seeded):
    run = prepare(seeded)

    assert run.inventory_repos == ["facturas", "infra"]
    assert run.organization == ORG


def test_repos_con_prefijo_de_la_organizacion_se_normalizan_sin_distinguir_mayusculas(seeded):
    set_repos(seeded, ["Taller-Norte/facturas", "taller-norte/infra"])

    assert prepare(seeded).inventory_repos == ["facturas", "infra"]


def test_repo_de_otra_organizacion_se_rechaza(seeded):
    set_repos(seeded, ["facturas", "otra-org/infra"])

    with pytest.raises(WorkspaceError, match="no pertenece a la organización"):
        prepare(seeded)


def test_asset_id_inexistente_se_rechaza(seeded):
    with pytest.raises(WorkspaceError, match="asset_id.*nope"):
        prepare(seeded, asset_id="nope")


def test_asset_id_de_otro_tipo_se_rechaza(seeded):
    with pytest.raises(WorkspaceError, match="code_hosting.*srv-app"):
        prepare(seeded, asset_id="srv-app")
