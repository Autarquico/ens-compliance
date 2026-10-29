"""`ens-evidence collect restic`: códigos de salida 0/3/1 y evidencia guardada (ejecutor falso)."""

import logging

import pytest

from ens_evidence import __version__
from ens_evidence.evidence import list_manifests
from ens_evidence.models.enums import CollectionStatus

from .cli_support import install_executor, run_collect, stored_result, write_workspace
from .fake_restic import CANARY, NOW, PASSWORD, REPOSITORY_ID, FakeRestic, snapshot_json


@pytest.fixture(autouse=True)
def password(monkeypatch):
    monkeypatch.setenv("ENS_BACKUP_RESTIC_PASSWORD", PASSWORD)


@pytest.fixture
def workspace(tmp_path):
    return write_workspace(tmp_path, "s3:s3.example.com/copias")


def test_todo_recogido_sale_con_0_y_guarda_la_evidencia(workspace, monkeypatch):
    settings, store = workspace
    install_executor(monkeypatch, FakeRestic().with_snapshots(snapshot_json()))

    result = run_collect(settings)

    assert result.exit_code == 0, result.output
    (manifest,) = list_manifests(store).manifests
    assert manifest.source == "restic"
    assert manifest.asset_id == "bk-diario"
    assert manifest.provenance.kind.value == "collector"
    assert manifest.provenance.submitted_by == "collector:restic"
    assert manifest.collector.version == __version__
    assert manifest.collected_at == NOW
    assert f"Evidencia registrada: {manifest.id}" in result.output
    stored, _ = stored_result(store)
    assert stored.data["repository_id"] == REPOSITORY_ID
    assert {s["status"] for s in stored.data["sections"].values()} == {"SUCCESS"}


def test_imprime_el_estado_de_cada_seccion(workspace, monkeypatch):
    settings, _ = workspace
    install_executor(monkeypatch, FakeRestic())

    result = run_collect(settings)

    for name in ("repository", "snapshots", "runs", "integrity_checks", "restore_tests"):
        assert f"  {name}: SUCCESS" in result.output


def test_una_seccion_fallida_sale_con_3_y_guarda_la_evidencia(workspace, monkeypatch):
    settings, store = workspace
    install_executor(monkeypatch, FakeRestic(snapshots=(1, CANARY)))

    result = run_collect(settings)

    assert result.exit_code == 3, result.output
    assert "  snapshots: ERROR" in result.output
    stored, _ = stored_result(store)
    assert stored.collection_status is CollectionStatus.SUCCESS
    assert stored.data["sections"]["snapshots"]["status"] == "ERROR"


def test_un_repositorio_inexistente_sale_con_0_y_guarda_exists_false(workspace, monkeypatch):
    settings, store = workspace
    install_executor(monkeypatch, FakeRestic(cat=(10, "")))

    result = run_collect(settings)

    assert result.exit_code == 0, result.output
    stored, _ = stored_result(store)
    assert stored.data["sections"]["repository"]["items"][0]["exists"] is False


def test_contrasena_incorrecta_sale_con_1_y_guarda_la_evidencia_de_error(workspace, monkeypatch):
    settings, store = workspace
    install_executor(monkeypatch, FakeRestic(cat=(12, f"{CANARY} {PASSWORD}")))

    result = run_collect(settings)

    assert result.exit_code == 1
    assert "12" in result.output
    (manifest,) = list_manifests(store).manifests
    stored, _ = stored_result(store)
    assert stored.collection_status is CollectionStatus.ERROR
    assert manifest.id in result.output


def test_sin_contrasena_sale_con_1_y_dice_que_variable_falta(workspace, monkeypatch):
    settings, store = workspace
    monkeypatch.delenv("ENS_BACKUP_RESTIC_PASSWORD")

    result = run_collect(settings)

    assert result.exit_code == 1
    assert "ENS_BACKUP_RESTIC_PASSWORD" in result.output
    assert stored_result(store)[0].collection_status is CollectionStatus.ERROR


def test_sin_configuracion_del_conector_sale_con_error_y_no_guarda_nada(tmp_path, monkeypatch):
    settings, store = write_workspace(tmp_path, "s3:s3.example.com/copias")
    (tmp_path / "ws" / "connectors" / "restic.yaml").unlink()
    install_executor(monkeypatch, FakeRestic())

    result = run_collect(settings)

    assert result.exit_code != 0
    assert "connectors/restic.yaml" in result.output
    assert list_manifests(store).manifests == []


@pytest.mark.parametrize(
    "cat",
    [(12, f"{CANARY} {PASSWORD}"), (1, f"{CANARY} {PASSWORD}"), (0, f"{CANARY} {PASSWORD}")],
)
def test_la_salida_de_la_cli_y_los_logs_no_contienen_secretos(workspace, monkeypatch, caplog, cat):
    settings, store = workspace
    caplog.set_level(logging.DEBUG)
    install_executor(monkeypatch, FakeRestic(cat=cat))

    result = run_collect(settings)

    everything = result.output + "".join(r.getMessage() for r in caplog.records)
    everything += "".join(store.read_bytes(key).decode() for key in store.list("evidence"))
    assert CANARY not in everything
    assert PASSWORD not in everything
