import json
from datetime import UTC, datetime

from ens_evidence.backup import BackupRun, dump, list_restores, list_runs
from ens_evidence.backup.records import run_key

from .conftest import CANARY, ens

STARTED = datetime(2026, 1, 1, tzinfo=UTC)


def test_backup_run_con_el_repositorio_inaccesible_queda_failed_y_con_su_error(
    settings, workspace, scripted_restic
):
    scripted_restic.reply("cat", err=CANARY, code=1)
    scripted_restic.reply("backup", err=CANARY, code=1)

    result = ens(settings, "backup", "run")

    assert result.exit_code == 1
    (run,) = list_runs(workspace)[0]
    assert run.status == "failed"
    assert run.repository_id is None
    assert run.error and "código 1" in run.error
    assert run.finished_at is not None


def test_restore_que_falla_en_restic_queda_failed_no_started(
    settings, workspace, scripted_restic, tmp_path
):
    previous = BackupRun(
        run_id="BK-20260101T000000Z-aaaaaa",
        status="failed",
        started_at=STARTED,
        finished_at=STARTED,
        repository="/copia",
        error="x",
    )
    workspace.create_bytes(run_key(previous.run_id), dump(previous))
    scripted_restic.reply(
        "snapshots", out=json.dumps([{"id": "a" * 64, "time": "2026-01-01T00:00:00Z"}])
    )
    scripted_restic.reply("restore", err=CANARY, code=1)

    result = ens(
        settings, "backup", "restore", "--snapshot", "latest", "--target", str(tmp_path / "out")
    )

    assert result.exit_code == 2
    (restore,) = list_restores(workspace)[0]
    assert restore.status == "failed"
    assert restore.failure == "restore_failed"


def test_restore_en_un_destino_no_vacio_no_deja_registro(
    settings, workspace, scripted_restic, tmp_path
):
    previous = BackupRun(
        run_id="BK-20260101T000000Z-aaaaaa",
        status="failed",
        started_at=STARTED,
        finished_at=STARTED,
        repository="/copia",
        error="x",
    )
    workspace.create_bytes(run_key(previous.run_id), dump(previous))
    target = tmp_path / "out"
    target.mkdir()
    (target / "algo").write_text("x")

    result = ens(settings, "backup", "restore", "--snapshot", "latest", "--target", str(target))

    assert result.exit_code == 2
    assert list_restores(workspace)[0] == []
