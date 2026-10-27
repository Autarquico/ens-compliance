import shutil
from datetime import UTC, datetime

import pytest
import yaml

from ens_evidence.backup import (
    BackupError,
    BackupRun,
    BackupSecrets,
    Restic,
    dump,
    list_runs,
    run_backup,
)
from ens_evidence.backup.records import run_key
from ens_evidence.backup.runner import inventory_of
from ens_evidence.workspace import AlreadyExists, LocalStore

from .conftest import PASSWORD, ens, import_evidences

pytestmark = pytest.mark.usefixtures("real_restic")


def records(workspace: LocalStore) -> list[BackupRun]:
    runs, unreadable = list_runs(workspace)
    assert unreadable == []
    return runs


def test_storage_caido_registra_failed_sin_secretos_y_nunca_completed(
    settings, workspace, documents, repository
):
    import_evidences(settings, documents)
    assert ens(settings, "backup", "init").exit_code == 0
    shutil.rmtree(repository)

    result = ens(settings, "backup", "run")

    assert result.exit_code == 1
    (run,) = records(workspace)
    assert run.status == "failed"
    assert run.snapshot_id is None and run.inventory is None
    assert run.error
    assert PASSWORD not in result.output
    assert PASSWORD not in workspace.read_text(run_key(run.run_id))


def test_storage_caido_no_da_la_evidencia_por_copiada_en_status(
    settings, workspace, documents, repository
):
    import_evidences(settings, documents)
    ens(settings, "backup", "init")
    shutil.rmtree(repository)
    ens(settings, "backup", "run")

    status = ens(settings, "backup", "status")

    assert status.exit_code == 1
    assert "failed" in status.output
    assert not any(run.status == "completed" for run in records(workspace))


def test_repositorio_sin_inicializar_falla_sin_completar(settings, workspace, documents):
    import_evidences(settings, documents)

    result = ens(settings, "backup", "run")

    assert result.exit_code == 1
    assert [run.status for run in records(workspace)] == ["failed"]


def test_restauracion_completa_devuelve_inventario_y_hashes_iguales(
    settings, workspace, documents, tmp_path
):
    import_evidences(settings, documents)
    assert ens(settings, "backup", "init").exit_code == 0
    assert ens(settings, "backup", "run").exit_code == 0
    (run,) = records(workspace)
    assert run.status == "completed" and run.inventory
    recorded = run.inventory
    shutil.rmtree(tmp_path / "ws")
    restored = tmp_path / "restored"

    restore = ens(settings, "backup", "restore", "--snapshot", "latest", "--target", str(restored))
    imported = ens(settings, "workspace", "import", "--all", str(restored))

    assert restore.exit_code == 0, restore.output
    assert imported.exit_code == 0, imported.output
    rebuilt = inventory_of(LocalStore(tmp_path / "ws"))
    assert rebuilt.pop(run_key(run.run_id)) is not None
    assert rebuilt == recorded
    verify = ens(settings, "evidence", "verify", "--all")
    assert verify.exit_code == 0, verify.output


def test_restauracion_con_workspace_presente_confirma_el_inventario_registrado(
    settings, documents, tmp_path
):
    import_evidences(settings, documents)
    ens(settings, "backup", "init")
    ens(settings, "backup", "run")

    restore = ens(
        settings, "backup", "restore", "--snapshot", "latest", "--target", str(tmp_path / "r")
    )

    assert restore.exit_code == 0, restore.output
    assert "coincide con el registrado" in restore.output


def test_restauracion_a_carpeta_no_vacia_se_rechaza(settings, documents, tmp_path):
    import_evidences(settings, documents)
    ens(settings, "backup", "init")
    ens(settings, "backup", "run")
    target = tmp_path / "ocupada"
    target.mkdir()
    (target / "x").write_text("x")

    restore = ens(settings, "backup", "restore", "--snapshot", "latest", "--target", str(target))

    assert restore.exit_code != 0
    assert sorted(p.name for p in target.iterdir()) == ["x"]


def test_backup_init_es_idempotente_y_no_toca_un_repositorio_existente(
    settings, documents, repository
):
    import_evidences(settings, documents)
    assert ens(settings, "backup", "init").exit_code == 0
    ens(settings, "backup", "run")
    config = (repository / "config").read_bytes()
    snapshots = sorted(p.name for p in (repository / "snapshots").iterdir())

    again = ens(settings, "backup", "init")

    assert again.exit_code == 0, again.output
    assert "ya existe" in again.output
    assert (repository / "config").read_bytes() == config
    assert sorted(p.name for p in (repository / "snapshots").iterdir()) == snapshots


def test_backup_init_con_contrasena_erronea_no_reinicializa(settings, documents, monkeypatch):
    ens(settings, "backup", "init")
    monkeypatch.setenv("ENS_BACKUP_RESTIC_PASSWORD", "otra-contrasena-distinta")

    result = ens(settings, "backup", "init")

    assert result.exit_code != 0
    assert "otra-contrasena-distinta" not in result.output


def test_ejecucion_interrumpida_se_informa_como_interrumpida(settings, workspace, documents):
    import_evidences(settings, documents)
    ens(settings, "backup", "init")
    assert ens(settings, "backup", "run").exit_code == 0
    started = BackupRun.model_validate(
        {
            "run_id": "BK-20260601T000000Z-dead01",
            "status": "started",
            "started_at": "2026-06-01T00:00:00+00:00",
            "repository": "irrelevante",
        }
    )
    workspace.create_bytes(run_key(started.run_id), dump(started))

    status = ens(settings, "backup", "status")

    assert status.exit_code == 1
    assert "Interrumpida" in status.output
    assert started.run_id in status.output


class FailingFinalWrite(LocalStore):
    """Frontera de almacenamiento: el registro final no se puede escribir."""

    def write_bytes(self, key: str, data: bytes) -> None:
        if key.startswith("evidence/backups/"):
            raise OSError("disco lleno")
        super().write_bytes(key, data)


def test_fallo_al_escribir_el_registro_final_hace_fallar_backup_run(
    tmp_path, settings, documents, repository
):
    import_evidences(settings, documents)
    ens(settings, "backup", "init")
    store = FailingFinalWrite(tmp_path / "ws")
    secrets = BackupSecrets(PASSWORD)
    restic = Restic(str(repository), secrets)

    with pytest.raises(BackupError, match="registro final"):
        run_backup(store, restic, str(repository), secrets, lambda: datetime.now(UTC))

    (run,) = records(store)
    assert run.status == "started"
    assert ens(settings, "backup", "status").exit_code == 1


def test_run_registra_started_antes_de_copiar_con_creacion_exclusiva(
    settings, workspace, documents
):
    import_evidences(settings, documents)
    ens(settings, "backup", "init")
    ens(settings, "backup", "run")
    (run,) = records(workspace)

    with pytest.raises(AlreadyExists):
        workspace.create_bytes(run_key(run.run_id), b"otro")
    assert yaml.safe_load(workspace.read_bytes(run_key(run.run_id)))["status"] == "completed"
