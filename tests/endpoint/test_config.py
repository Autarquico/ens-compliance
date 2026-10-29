"""`connectors/endpoint.yaml`: antigüedad obligatoria, credenciales y sincronización."""

import pytest
import yaml

from ens_evidence.cli import app
from ens_evidence.connectors.config import (
    ConnectorConfigError,
    connector_config_key,
    load_connector_config,
)
from ens_evidence.endpoint.config import EndpointConfig
from ens_evidence.scope.sync import SYNCED_KEYS
from ens_evidence.workspace import LocalStore

from .support import ASSETS, CONFIG, import_package, persisted_keys

KEY = connector_config_key("endpoint")


def load(store, config: dict) -> EndpointConfig:
    store.write_text(KEY, yaml.safe_dump(config))
    return load_connector_config(store, "endpoint", EndpointConfig)


def test_la_configuracion_completa_se_acepta(store):
    config = load(store, CONFIG)

    assert config.max_evidence_age_days == 30
    assert set(config.hosts) == {"lap-ana", "srv-app"}


def test_sin_max_evidence_age_days_se_rechaza_sin_valor_por_defecto(store):
    config = {k: v for k, v in CONFIG.items() if k != "max_evidence_age_days"}

    with pytest.raises(ConnectorConfigError, match="max_evidence_age_days"):
        load(store, config)


@pytest.mark.parametrize("days", [0, -1, "30", 1.5, None, True])
def test_una_antiguedad_maxima_no_positiva_o_no_entera_se_rechaza(store, days):
    with pytest.raises(ConnectorConfigError, match="max_evidence_age_days"):
        load(store, {**CONFIG, "max_evidence_age_days": days})


@pytest.mark.parametrize("key", ["password", "api_token", "secret_key"])
def test_las_credenciales_en_la_configuracion_se_rechazan(store, key):
    with pytest.raises(ConnectorConfigError, match="credenciales"):
        load(store, {**CONFIG, key: "valor"})


def test_una_inscripcion_con_uuid_o_hostname_repetido_se_rechaza(store):
    lap = CONFIG["hosts"]["lap-ana"]
    for clash in ({**lap, "hostname": "otro"}, {**lap, "hardware_uuid": "otro"}):
        hosts = {"lap-ana": lap, "srv-app": clash}
        with pytest.raises(ConnectorConfigError, match="mismo"):
            load(store, {**CONFIG, "hosts": hosts})


def test_una_version_no_numerica_se_rechaza(store):
    for field in ("release", "min_version"):
        bad = [{"platform": "darwin", "name": "macOS", "release": "15", field: "Sonoma"}]

        with pytest.raises(ConnectorConfigError, match=field):
            load(store, {**CONFIG, "supported_os": bad})


def test_dos_entradas_de_la_misma_release_se_rechazan_aunque_difieran_en_el_minimo(store):
    mac = {"platform": "darwin", "name": "macOS", "release": "15"}
    overlapping = [{**mac, "end_of_support": "2026-10-02"}, {**mac, "min_version": "15.3"}]

    with pytest.raises(ConnectorConfigError, match="misma platform, name y release"):
        load(store, {**CONFIG, "supported_os": overlapping})


def test_la_misma_release_con_otro_nombre_o_formato_equivalente_se_detecta(store):
    ubuntu = {"platform": "linux", "name": "Ubuntu", "release": "22.04"}
    clash = [ubuntu, {**ubuntu, "name": "ubuntu", "release": "22.4"}]

    with pytest.raises(ConnectorConfigError, match="misma platform, name y release"):
        load(store, {**CONFIG, "supported_os": clash})


@pytest.mark.parametrize(
    "entry",
    [
        {"name": "Ubuntu", "platform": "linux", "release": "22"},
        {"name": "macOS", "platform": "darwin", "release": "15.3"},
        {"name": "macOS", "platform": "darwin", "release": "15", "min_version": "14.7"},
    ],
)
def test_una_release_mal_formada_o_un_minimo_de_otra_release_se_rechaza(store, entry):
    with pytest.raises(ConnectorConfigError):
        load(store, {**CONFIG, "supported_os": [entry]})


def test_sin_configuracion_la_importacion_se_rechaza_y_no_persiste(tmp_path, laptop_package):
    bare = LocalStore(tmp_path / "bare")
    bare.write_text("scope/assets.yaml", ASSETS)

    with pytest.raises(ConnectorConfigError):
        import_package(bare, laptop_package)
    assert persisted_keys(bare) == []


def test_la_configuracion_esta_entre_las_claves_sincronizadas():
    assert KEY in SYNCED_KEYS


def test_pull_trae_la_configuracion_del_endpoint(tmp_path, settings, store):
    from typer.testing import CliRunner

    target = tmp_path / "local"

    result = CliRunner().invoke(
        app, ["--settings", str(settings), "workspace", "pull", str(target)]
    )

    assert result.exit_code == 0, result.output
    assert yaml.safe_load((target / KEY).read_text()) == CONFIG
