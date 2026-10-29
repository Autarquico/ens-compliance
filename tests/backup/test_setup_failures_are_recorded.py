"""Sin contraseña (o sin ajustes) `run`, `check` y `restore` dejan un intento `failed` sin id."""

import json
from datetime import timedelta

import pytest
import yaml
from connectors.restic.check_data import run as evaluate
from connectors.restic.fake_restic import NOW, FakeRestic, snapshot_json
from connectors.restic.record_data import CONFIG, backup_run, integrity_check

from ens_evidence.backup import (
    PASSWORD_VAR,
    dump,
    list_checks,
    list_restores,
    list_runs,
)
from ens_evidence.backup.records import check_key, run_key
from ens_evidence.connectors.restic import ResticConnector
from ens_evidence.connectors.restic.checks import backup_002_no_errors, backup_004_integrity
from ens_evidence.connectors.restic.config import ResticRun, WorkspaceRecords
from ens_evidence.models.enums import CheckStatus
from ens_evidence.workspace.settings import BackupSettings

from .conftest import BACKUP_KEY, BACKUP_SECRET, PASSWORD, ens

SETUP_CODES = {"invalid_credentials", "invalid_settings"}


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr("ens_evidence.cli.clock", lambda: NOW - timedelta(minutes=30))


@pytest.fixture
def prior_success(workspace):
    run = backup_run("BK-20260101T000000Z-aaaaaa")
    check = integrity_check("CK-20260101T000000Z-bbbbbb")
    workspace.create_bytes(run_key(run.run_id), dump(run))
    workspace.create_bytes(check_key(check.check_id), dump(check))


@pytest.fixture
def without_password(monkeypatch):
    monkeypatch.delenv(PASSWORD_VAR)


def collect_and_check(workspace, check):
    runs, _ = list_runs(workspace)
    checks, _ = list_checks(workspace)
    connector = ResticConnector(
        backup=BackupSettings(restic_repository="repo"),
        executor=FakeRestic().with_snapshots(snapshot_json()),
        clock=lambda: NOW,
    )
    records = WorkspaceRecords(runs=runs, checks=checks)
    return evaluate(check, connector.collect(ResticRun(**CONFIG, records=records)))


def test_run_sin_contrasena_registra_un_intento_failed_sin_id(
    settings, workspace, prior_success, without_password
):
    result = ens(settings, "backup", "run")

    assert result.exit_code == 2
    latest = list_runs(workspace)[0][-1]
    assert (latest.status, latest.repository_id, latest.error) == (
        "failed",
        None,
        "invalid_credentials",
    )
    assert latest.snapshot_id is None
    assert PASSWORD not in result.output and PASSWORD_VAR not in latest.error


def test_check_sin_contrasena_registra_un_intento_failed_sin_id(
    settings, workspace, prior_success, without_password
):
    result = ens(settings, "backup", "check", "--read-data-subset", "10%")

    assert result.exit_code == 2
    latest = list_checks(workspace)[0][-1]
    assert (latest.status, latest.repository_id, latest.failure, latest.scope) == (
        "failed",
        None,
        "invalid_credentials",
        "subset:10%",
    )


def test_restore_sin_contrasena_registra_un_intento_failed_sin_id(
    settings, workspace, prior_success, without_password, tmp_path
):
    result = ens(
        settings, "backup", "restore", "--snapshot", "latest", "--target", str(tmp_path / "out")
    )

    assert result.exit_code == 2
    (restore,) = list_restores(workspace)[0]
    assert (restore.status, restore.repository_id, restore.failure) == (
        "failed",
        None,
        "invalid_credentials",
    )


def test_restore_en_un_workspace_sin_copias_no_deja_registro(
    settings, workspace, without_password, tmp_path
):
    ens(settings, "backup", "restore", "--snapshot", "latest", "--target", str(tmp_path / "out"))

    assert list_restores(workspace)[0] == []


def test_run_sin_contrasena_hace_que_002_no_de_pass(
    settings, workspace, prior_success, without_password
):
    ens(settings, "backup", "run")

    result = collect_and_check(workspace, backup_002_no_errors)

    assert result.status is not CheckStatus.PASS


def test_check_sin_contrasena_hace_que_004_no_de_pass(
    settings, workspace, prior_success, without_password
):
    ens(settings, "backup", "check")

    result = collect_and_check(workspace, backup_004_integrity)

    assert result.status is not CheckStatus.PASS


def test_con_solo_el_exito_previo_002_y_004_dan_pass(workspace, prior_success):
    assert collect_and_check(workspace, backup_002_no_errors).status is CheckStatus.PASS
    assert collect_and_check(workspace, backup_004_integrity).status is CheckStatus.PASS


def test_claves_de_s3_solo_a_medias_registra_credenciales_invalidas_sin_valores(
    settings, workspace, prior_success, monkeypatch
):
    monkeypatch.setenv("ENS_BACKUP_AWS_ACCESS_KEY_ID", BACKUP_KEY)

    result = ens(settings, "backup", "run")

    assert result.exit_code == 2
    record = (settings.parent / "ws").rglob("BK-*")
    latest = list_runs(workspace)[0][-1]
    assert latest.error == "invalid_credentials"
    assert all(
        secret not in p.read_text() for p in record for secret in (BACKUP_KEY, BACKUP_SECRET)
    )
    assert BACKUP_KEY not in result.output


def test_sin_seccion_backup_en_los_ajustes_registra_ajustes_invalidos(
    settings, workspace, prior_success
):
    data = yaml.safe_load(settings.read_text())
    del data["backup"]
    settings.write_text(yaml.safe_dump(data))

    result = ens(settings, "backup", "check")

    assert result.exit_code == 2
    latest = list_checks(workspace)[0][-1]
    assert (latest.status, latest.failure, latest.repository_id) == (
        "failed",
        "invalid_settings",
        None,
    )


def test_si_el_workspace_no_se_puede_abrir_el_comportamiento_es_el_de_siempre(
    settings, without_password, tmp_path
):
    blocker = tmp_path / "es-un-fichero"
    blocker.write_text("x")
    data = yaml.safe_load(settings.read_text())
    data["workspace"]["local"]["path"] = str(blocker)
    settings.write_text(yaml.safe_dump(data))

    result = ens(settings, "backup", "run")

    assert result.exit_code == 2
    assert blocker.read_text() == "x"


def test_los_codigos_de_fallo_de_preparacion_estan_en_el_esquema():
    from ens_evidence.backup.records import IntegrityCheck, RestoreRecord

    for model in (IntegrityCheck, RestoreRecord):
        enum = json.dumps(model.model_json_schema()["properties"]["failure"])
        assert all(code in enum for code in SETUP_CODES)
