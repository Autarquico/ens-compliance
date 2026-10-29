import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from ens_evidence.backup import BackupRun, BackupSecrets, Restic, dump, list_restores, list_runs
from ens_evidence.backup.records import restore_key, run_key
from ens_evidence.workspace import LocalStore

from .conftest import PASSWORD, build_backed_up, ens, restore_backed_up

FULL_ID = re.compile(r"[0-9a-f]{64}")


@pytest.fixture(scope="module")
def template(real_restic, tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("backed-up")
    build_backed_up(root)
    return root


@pytest.fixture
def backed_up(template, closed_stdin, settings, workspace, tmp_path) -> BackupRun:
    restore_backed_up(template, tmp_path)
    (run,) = list_runs(workspace)[0]
    return run


def repository_id(repository) -> str:
    return Restic(str(repository), BackupSecrets(PASSWORD)).repository_id()


def only_restore(workspace):
    restores, unreadable = list_restores(workspace)
    assert unreadable == []
    assert len(restores) == 1
    return restores[0]


def test_backup_run_guarda_el_id_del_repositorio(backed_up, repository):
    assert backed_up.repository_id == repository_id(repository)
    assert FULL_ID.fullmatch(backed_up.repository_id)


def test_un_registro_de_copia_anterior_sin_repository_id_se_sigue_leyendo(workspace):
    old = BackupRun(
        run_id="BK-20260101T000000Z-aaaaaa",
        status="completed",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        finished_at=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
        repository="/copia",
        snapshot_id="abcd1234",
        inventory={"scope/a.yaml": "0" * 64},
    )
    data = yaml.safe_load(dump(old))
    del data["repository_id"]
    workspace.create_bytes(run_key(old.run_id), yaml.safe_dump(data).encode())

    runs, unreadable = list_runs(workspace)

    assert unreadable == []
    assert [(r.run_id, r.repository_id) for r in runs] == [(old.run_id, None)]


def test_restore_registra_repositorio_snapshot_destino_e_inventario_verificado(
    settings, workspace, backed_up, repository, tmp_path
):
    target = tmp_path / "restored"

    result = ens(settings, "backup", "restore", "--snapshot", "latest", "--target", str(target))

    assert result.exit_code == 0, result.output
    assert "coincide con el registrado" in result.output
    restore = only_restore(workspace)
    assert restore.status == "completed"
    assert restore.repository_id == repository_id(repository)
    assert FULL_ID.fullmatch(restore.snapshot_id)
    assert restore.snapshot_id.startswith(backed_up.snapshot_id)
    assert restore.target == str(target.resolve())
    assert (restore.inventory_verified, restore.mismatches) == (True, 0)
    assert restore.finished_at is not None and restore.started_at <= restore.finished_at
    assert restore.started_at.utcoffset().total_seconds() == 0


def test_restore_con_inventario_distinto_se_registra_como_no_verificado(
    settings, workspace, backed_up, tmp_path
):
    tampered = backed_up.model_copy(
        update={"inventory": {**backed_up.inventory, "scope/a.yaml": "0" * 64}}
    )
    workspace.write_bytes(run_key(backed_up.run_id), dump(tampered))

    ens(settings, "backup", "restore", "--snapshot", "latest", "--target", str(tmp_path / "r"))

    restore = only_restore(workspace)
    assert restore.status == "completed"
    assert restore.inventory_verified is False
    assert restore.mismatches >= 1


def test_restore_de_un_snapshot_inexistente_registra_failed(
    settings, workspace, backed_up, tmp_path
):
    result = ens(
        settings,
        "backup",
        "restore",
        "--snapshot",
        "deadbeef",
        "--target",
        str(tmp_path / "r"),
    )

    assert result.exit_code != 0
    restore = only_restore(workspace)
    assert (restore.status, restore.failure) == ("failed", "restore_failed")
    assert restore.snapshot_id is None and restore.inventory_verified is None


def test_restore_en_una_caja_vacia_sin_copias_registradas_no_escribe_registro(
    settings, backed_up, tmp_path
):
    shutil.rmtree(tmp_path / "ws")

    result = ens(
        settings, "backup", "restore", "--snapshot", "latest", "--target", str(tmp_path / "r")
    )

    assert result.exit_code == 0, result.output
    assert "no se registra" in result.output
    assert LocalStore(tmp_path / "ws").list("") == []


def test_restore_no_pisa_un_registro_de_restauracion_existente(
    settings, workspace, backed_up, tmp_path
):
    ens(settings, "backup", "restore", "--snapshot", "latest", "--target", str(tmp_path / "a"))
    first = only_restore(workspace)
    before = workspace.read_bytes(restore_key(first.restore_id))

    ens(settings, "backup", "restore", "--snapshot", "latest", "--target", str(tmp_path / "b"))

    assert workspace.read_bytes(restore_key(first.restore_id)) == before
    assert len(list_restores(workspace)[0]) == 2
