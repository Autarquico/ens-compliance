"""`connectors/restic.yaml`: umbrales obligatorios, `offsite` y credenciales."""

import pytest
import yaml
from pydantic import ValidationError

from ens_evidence.connectors.config import ConnectorConfigError, load_connector_config
from ens_evidence.connectors.restic import ResticConnector
from ens_evidence.connectors.restic.config import ResticConfig
from ens_evidence.workspace import LocalStore, WorkspaceError

from ..github.cli_support import EXAMPLE
from .record_data import CONFIG, OFFSITE, THRESHOLDS


@pytest.fixture
def store(tmp_path) -> LocalStore:
    store = LocalStore(tmp_path / "ws")
    for source in (EXAMPLE / "scope").iterdir():
        store.write_text(f"scope/{source.name}", source.read_text())
    return store


def write_config(store, config: dict) -> None:
    store.write_text("connectors/restic.yaml", yaml.safe_dump(config))


def load(store, config: dict) -> ResticConfig:
    write_config(store, config)
    return load_connector_config(store, "restic", ResticConfig)


def rejected(store, config: dict) -> str:
    with pytest.raises(ConnectorConfigError) as error:
        load(store, config)
    return str(error.value)


def test_la_configuracion_completa_se_acepta(store):
    config = load(store, {**CONFIG, "offsite": OFFSITE})

    assert config.max_backup_age_hours == 26
    assert config.offsite is not None


def test_sin_offsite_la_configuracion_es_valida(store):
    assert load(store, CONFIG).offsite is None


@pytest.mark.parametrize("name", [*THRESHOLDS, "asset_id"])
def test_cada_umbral_es_obligatorio_y_no_tiene_valor_por_defecto(store, name):
    config = {k: v for k, v in CONFIG.items() if k != name}

    assert name in rejected(store, config)


@pytest.mark.parametrize(
    "name", ["max_backup_age_hours", "max_check_age_days", "restore_test_validity_days"]
)
@pytest.mark.parametrize("value", [0, -1, -26, 1.5, "26", True, None])
def test_los_umbrales_de_edad_son_enteros_positivos(store, name, value):
    assert name in rejected(store, {**CONFIG, name: value})


@pytest.mark.parametrize("value", [-1, 101, 1000, 5.5, "5", True, None])
def test_la_cobertura_minima_del_check_debe_estar_entre_0_y_100(store, value):
    assert "min_check_read_data_percent" in rejected(
        store, {**CONFIG, "min_check_read_data_percent": value}
    )


@pytest.mark.parametrize("value", [0, 100])
def test_la_cobertura_minima_admite_los_extremos(store, value):
    config = load(store, {**CONFIG, "min_check_read_data_percent": value})

    assert config.min_check_read_data_percent == value


def test_un_campo_desconocido_se_rechaza(store):
    assert "inventado" in rejected(store, {**CONFIG, "inventado": 1})


@pytest.mark.parametrize(
    "change",
    [
        {"reviewed_at": "no-es-una-fecha"},
        {"reviewed_at": "2026-10-01"},
        {"reviewed_at": "2026-10-01T09:00:00"},
        {"valid_until": "mañana"},
        {"valid_until": "2027-10-01T09:00:00"},
        {"valid_until": "2026-10-01T09:00:00+00:00"},
        {"valid_until": "2026-09-01T09:00:00+00:00"},
        {"role": "cualquiera"},
        {"reviewer": ""},
        {"location": "   "},
        {"administrative_separation": None},
        {"extra": "x"},
    ],
    ids=lambda change: next(iter(change)) + "=" + str(next(iter(change.values()))),
)
def test_una_declaracion_offsite_invalida_se_rechaza(store, change):
    assert "offsite" in rejected(store, {**CONFIG, "offsite": {**OFFSITE, **change}})


def test_offsite_sin_alguno_de_sus_campos_se_rechaza(store):
    incomplete = {k: v for k, v in OFFSITE.items() if k != "reviewer"}

    assert "offsite.reviewer" in rejected(store, {**CONFIG, "offsite": incomplete})


@pytest.mark.parametrize(
    "credential",
    [
        {"password": "hunter2-canario"},
        {"restic_password": "hunter2-canario"},
        {"aws_secret_access_key": "hunter2-canario"},
        {"token": "ghp_" + "a" * 36},
    ],
)
def test_credenciales_en_el_yaml_se_rechazan_sin_repetir_el_valor(store, credential):
    message = rejected(store, {**CONFIG, **credential})

    assert "hunter2-canario" not in message
    assert "a" * 36 not in message


def test_un_activo_backup_del_inventario_se_acepta(store):
    config = load(store, CONFIG)

    prepared = ResticConnector().prepare(config, store)

    assert prepared.asset_id == "bk-diario"
    assert prepared.records.runs == []


def test_un_asset_id_inexistente_se_rechaza(store):
    config = load(store, {**CONFIG, "asset_id": "no-existe"})

    with pytest.raises(WorkspaceError, match="asset_id"):
        ResticConnector().prepare(config, store)


def test_un_asset_id_de_otro_tipo_se_rechaza(store):
    config = load(store, {**CONFIG, "asset_id": "db-facturas"})

    with pytest.raises(WorkspaceError, match="asset_id"):
        ResticConnector().prepare(config, store)


def test_el_modelo_no_acepta_umbrales_ausentes_al_construirlo_directamente():
    with pytest.raises(ValidationError):
        ResticConfig(asset_id="bk-diario")
