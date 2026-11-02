"""`run_configured_backup`: cada fallo esperable es un resultado visible, sin secretos."""

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ens_evidence.backup import (
    ACCESS_KEY_VAR,
    PASSWORD_VAR,
    SECRET_KEY_VAR,
    BackupOutcome,
    list_runs,
    run_configured_backup,
)
from ens_evidence.backup.records import BackupRun, dump, run_key
from ens_evidence.workspace import LocalStore
from ens_evidence.workspace.cycle_lock import CycleContext, CycleLock
from ens_evidence.workspace.settings import BackupSettings

from .conftest import BACKUP_KEY, BACKUP_SECRET, PASSWORD, WORKSPACE_KEY, WORKSPACE_SECRET

NOW = datetime(2026, 11, 1, 6, 0, tzinfo=UTC)
SECRETS = (PASSWORD, BACKUP_KEY, BACKUP_SECRET, WORKSPACE_KEY, WORKSPACE_SECRET)
SNAPSHOT = json.dumps({"message_type": "summary", "snapshot_id": "abcd1234"})


@pytest.fixture
def context(tmp_path):
    lock = CycleLock("identidad-de-prueba", tmp_path / "state").acquire()
    yield CycleContext(lock)
    lock.release()


@pytest.fixture
def env(repository) -> dict[str, str]:
    return {PASSWORD_VAR: PASSWORD}


@pytest.fixture
def configured(repository) -> BackupSettings:
    return BackupSettings(restic_repository=str(repository))


def run(store, configured, env, context) -> BackupOutcome:
    env = {**env, "PATH": os.environ["PATH"]}
    return run_configured_backup(store, configured, env, clock=lambda: NOW, context=context)


def assert_no_secrets(outcome: BackupOutcome, store: LocalStore, caplog) -> None:
    visible = [repr(outcome), caplog.text]
    visible += [
        store.read_text(key) for key in store.list("") if key.startswith("evidence/backups/")
    ]
    assert all(secret not in text for secret in SECRETS for text in visible)


def test_sin_ajustes_de_copia_se_omite_y_deja_un_intento_failed(workspace, env, context, caplog):
    outcome = run(workspace, None, env, context)

    assert (outcome.reason, outcome.run_key) == ("not_configured", None)
    (latest,) = list_runs(workspace)[0]
    assert (latest.status, latest.error) == ("failed", "invalid_settings")
    assert_no_secrets(outcome, workspace, caplog)


def test_sin_credenciales_falla_y_registra_credenciales_invalidas(
    workspace, configured, env, context, caplog
):
    del env[PASSWORD_VAR]

    outcome = run(workspace, configured, env, context)

    assert outcome.reason == "invalid_credentials"
    (latest,) = list_runs(workspace)[0]
    assert (latest.status, latest.error) == ("failed", "invalid_credentials")
    assert_no_secrets(outcome, workspace, caplog)


def test_credenciales_compartidas_con_el_workspace_se_rechazan(
    workspace, configured, env, context, caplog
):
    env |= {
        ACCESS_KEY_VAR: WORKSPACE_KEY,
        SECRET_KEY_VAR: WORKSPACE_SECRET,
        "AWS_ACCESS_KEY_ID": WORKSPACE_KEY,
        "AWS_SECRET_ACCESS_KEY": WORKSPACE_SECRET,
    }

    outcome = run(workspace, configured, env, context)

    assert outcome.reason == "invalid_credentials"
    assert list_runs(workspace)[0][0].error == "invalid_credentials"
    assert_no_secrets(outcome, workspace, caplog)


def test_fallo_de_restic_es_un_resultado_con_su_registro_failed(
    workspace, configured, env, context, scripted_restic, caplog
):
    scripted_restic.reply("backup", err=f"boom {PASSWORD}", code=1)

    outcome = run(workspace, configured, env, context)

    assert outcome.reason == "restic_failed"
    (record,) = list_runs(workspace)[0]
    assert (record.status, outcome.run_key) == ("failed", run_key(record.run_id))
    assert_no_secrets(outcome, workspace, caplog)


def test_copia_correcta_devuelve_ok_con_la_clave_del_registro(
    workspace, configured, env, context, scripted_restic
):
    scripted_restic.reply("backup", out=SNAPSHOT)

    outcome = run(workspace, configured, env, context)

    assert outcome.reason is None
    (record,) = list_runs(workspace)[0]
    assert (record.status, outcome.run_key) == ("completed", run_key(record.run_id))


class FailingFinalWrite(LocalStore):
    """Frontera de almacenamiento: el registro final no se puede escribir."""

    def write_bytes(self, key: str, data: bytes) -> None:
        if key.startswith("evidence/backups/"):
            raise OSError(f"disco lleno {PASSWORD}")
        super().write_bytes(key, data)


def test_fallo_al_persistir_el_registro_es_un_resultado_y_no_una_excepcion(
    tmp_path, configured, env, context, scripted_restic, caplog
):
    scripted_restic.reply("backup", out=SNAPSHOT)
    store = FailingFinalWrite(tmp_path / "ws")

    outcome = run(store, configured, env, context)

    assert outcome.reason == "record_not_persisted"
    (record,) = list_runs(store)[0]
    assert record.status == "started"
    assert_no_secrets(outcome, store, caplog)


def test_una_ejecucion_previa_sin_finalizar_impide_lanzar_otra(
    workspace, configured, env, context, scripted_restic, caplog
):
    started = BackupRun(
        run_id="BK-20260601T000000Z-dead01",
        status="started",
        started_at=datetime(2026, 6, 1, tzinfo=UTC),
        repository="irrelevante",
    )
    workspace.create_bytes(run_key(started.run_id), dump(started))

    outcome = run(workspace, configured, env, context)

    assert outcome.reason == "previous_run_unfinished"
    assert [r.run_id for r in list_runs(workspace)[0]] == [started.run_id]
    assert scripted_restic.calls() == []
    assert_no_secrets(outcome, workspace, caplog)


def test_el_lock_lo_pone_quien_llama_y_la_funcion_no_lo_toma(
    workspace, configured, env, context, scripted_restic
):
    scripted_restic.reply("backup", out=SNAPSHOT)

    run(workspace, configured, env, context)

    assert context.lock.held


def test_el_contexto_exige_un_lock_tomado(tmp_path: Path):
    with pytest.raises(ValueError):
        CycleContext(CycleLock("x", tmp_path))


@pytest.fixture(autouse=True)
def _log_everything(caplog):
    caplog.set_level(logging.DEBUG)
