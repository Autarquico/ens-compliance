import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from ens_evidence.backup import BackupRun, BackupSecrets, Restic, list_runs
from ens_evidence.backup.records import SnapshotInventory, run_key, snapshot_inventory_key
from ens_evidence.backup.runner import inventory_of
from ens_evidence.workspace import LocalStore

from .conftest import PASSWORD, ens, import_evidences

pytestmark = pytest.mark.usefixtures("real_restic")

META = ".ens-backup"


@pytest.fixture
def backed_up(settings, workspace, documents) -> BackupRun:
    import_evidences(settings, documents)
    assert ens(settings, "backup", "init").exit_code == 0
    assert ens(settings, "backup", "run").exit_code == 0
    (run,) = list_runs(workspace)[0]
    return run


def restore_to_lost_box(settings, tmp_path) -> Path:
    shutil.rmtree(tmp_path / "ws")
    restored = tmp_path / "restored"
    result = ens(settings, "backup", "restore", "--snapshot", "latest", "--target", str(restored))
    assert result.exit_code == 0, result.output
    return restored


def snapshot_of(folder: Path, repository: Path) -> None:
    Restic(str(repository), BackupSecrets(PASSWORD)).backup(folder)


def test_caja_perdida_restaura_y_verifica_con_el_inventario_del_snapshot(
    settings, backed_up, tmp_path
):
    shutil.rmtree(tmp_path / "ws")

    result = ens(
        settings, "backup", "restore", "--snapshot", "latest", "--target", str(tmp_path / "r")
    )

    assert result.exit_code == 0, result.output
    assert "coincide con el registrado" in result.output
    assert "NO verificada" not in result.output


def test_caja_perdida_con_un_fichero_distinto_al_inventario_no_se_da_por_buena(
    settings, backed_up, repository, tmp_path
):
    folder = tmp_path / "forged"
    (folder / META).mkdir(parents=True)
    (folder / "scope").mkdir()
    (folder / "scope" / "a.yaml").write_bytes(b"cambiado")
    inventory = SnapshotInventory(
        run_id="BK-20260601T000000Z-aaaaaa",
        started_at=datetime(2026, 6, 1, tzinfo=UTC),
        exported_at=datetime(2026, 6, 1, tzinfo=UTC),
        repository="x",
        inventory={"scope/a.yaml": "0" * 64},
    )
    (folder / snapshot_inventory_key(inventory.run_id)).write_text(inventory.model_dump_json())
    snapshot_of(folder, repository)
    shutil.rmtree(tmp_path / "ws")

    result = ens(
        settings, "backup", "restore", "--snapshot", "latest", "--target", str(tmp_path / "r")
    )

    assert result.exit_code != 0
    assert "scope/a.yaml" in result.output


def test_snapshot_sin_inventario_se_informa_no_verificado_y_sale_con_error(
    settings, backed_up, repository, tmp_path
):
    folder = tmp_path / "plain"
    folder.mkdir()
    (folder / "x.txt").write_text("x")
    snapshot_of(folder, repository)
    shutil.rmtree(tmp_path / "ws")

    result = ens(
        settings, "backup", "restore", "--snapshot", "latest", "--target", str(tmp_path / "r")
    )

    assert result.exit_code != 0
    assert "NO verificada" in result.output


def test_tras_restaurar_e_importar_status_muestra_la_ejecucion_reconstruida_no_interrumpida(
    settings, backed_up, tmp_path
):
    restored = restore_to_lost_box(settings, tmp_path)
    assert ens(settings, "workspace", "import", "--all", str(restored)).exit_code == 0

    status = ens(settings, "backup", "status")

    assert status.exit_code == 0, status.output
    assert "Interrumpida" not in status.output
    assert "completed" in status.output
    assert "(reconstruido al restaurar)" in status.output
    (run,) = list_runs(LocalStore(tmp_path / "ws"))[0]
    assert run.run_id == backed_up.run_id
    assert run.status == "completed"
    assert run.reconstructed_from_snapshot is True
    assert run.reconstructed_at is not None


def test_backup_run_nunca_marca_la_ejecucion_como_reconstruida(backed_up, workspace):
    stored = yaml.safe_load(workspace.read_bytes(run_key(backed_up.run_id)))

    assert backed_up.reconstructed_from_snapshot is False
    assert backed_up.reconstructed_at is None
    assert stored["reconstructed_from_snapshot"] is False
    assert stored["reconstructed_at"] is None


def test_el_snapshot_no_contiene_el_registro_started_de_su_propia_ejecucion(
    settings, backed_up, tmp_path
):
    restored = restore_to_lost_box(settings, tmp_path)

    keys = LocalStore(restored).list("")

    assert run_key(backed_up.run_id) not in keys
    assert snapshot_inventory_key(backed_up.run_id) in keys
    inventory = json.loads((restored / snapshot_inventory_key(backed_up.run_id)).read_bytes())
    assert run_key(backed_up.run_id) not in inventory["inventory"]


def test_ens_backup_nunca_llega_al_workspace(settings, backed_up, tmp_path):
    restored = restore_to_lost_box(settings, tmp_path)
    assert (restored / META).is_dir()

    assert ens(settings, "workspace", "import", "--all", str(restored)).exit_code == 0

    assert not any(key.startswith(f"{META}/") for key in inventory_of(LocalStore(tmp_path / "ws")))
    assert not (tmp_path / "ws" / META).exists()
