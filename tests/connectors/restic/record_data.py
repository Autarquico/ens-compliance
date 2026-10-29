"""Registros del workspace y configuración de una recogida, con valores coherentes por defecto."""

from datetime import timedelta
from typing import Any

from ens_evidence.backup.records import BackupRun, IntegrityCheck, RestoreRecord
from ens_evidence.backup.restore_test import RestoreTest
from ens_evidence.connectors.restic.config import ResticRun, WorkspaceRecords

from .fake_restic import NOW, REPOSITORY_ID, SNAPSHOT_ID

THRESHOLDS = {
    "max_backup_age_hours": 26,
    "max_check_age_days": 35,
    "restore_test_validity_days": 90,
    "min_check_read_data_percent": 5,
}
OFFSITE = {
    "location": "Oficina de Madrid, otra cuenta de Cloudflare",
    "administrative_separation": "Cuenta de Cloudflare distinta, administrada por otra persona",
    "reviewer": "Ana",
    "role": "security_officer",
    "reviewed_at": "2026-10-01T09:00:00+00:00",
    "valid_until": "2027-10-01T09:00:00+00:00",
}
CONFIG = {"asset_id": "bk-diario", **THRESHOLDS}


def backup_run(
    run_id: str = "r1",
    status: str = "completed",
    *,
    age: timedelta = timedelta(hours=2),
    repository_id: str | None = REPOSITORY_ID,
    snapshot_id: str = SNAPSHOT_ID,
) -> BackupRun:
    started = NOW - age
    common: dict[str, Any] = {
        "run_id": run_id,
        "status": status,
        "started_at": started,
        "repository": "repo",
        "repository_id": repository_id,
    }
    finished = {"finished_at": started + timedelta(minutes=1)}
    match status:
        case "completed":
            extra = {**finished, "snapshot_id": snapshot_id, "inventory": {}}
        case "failed":
            extra = {**finished, "error": "fallo de la copia"}
        case _:
            extra = {}
    return BackupRun(**common, **extra)


def integrity_check(
    check_id: str = "c1",
    status: str = "completed",
    *,
    age: timedelta = timedelta(days=3),
    scope: str = "subset:10%",
    repository_id: str | None = REPOSITORY_ID,
) -> IntegrityCheck:
    started = NOW - age
    common: dict[str, Any] = {
        "check_id": check_id,
        "status": status,
        "started_at": started,
        "repository_id": repository_id,
        "scope": scope,
    }
    finished = {"finished_at": started + timedelta(minutes=1)}
    match status:
        case "completed":
            extra = {**finished, "exit_code": 0, "num_errors": 0, "broken_packs": 0}
        case "failed":
            extra = {**finished, "failure": "repository_errors", "exit_code": 1, "num_errors": 2}
            if repository_id is None:
                extra = {**finished, "failure": "restic_unavailable"}
        case _:
            extra = {}
    return IntegrityCheck(**common, **extra)


def restore_record(
    restore_id: str = "rs1",
    *,
    age: timedelta = timedelta(days=10, hours=1),
    repository_id: str = REPOSITORY_ID,
    snapshot_id: str = SNAPSHOT_ID,
    inventory_verified: bool = True,
) -> RestoreRecord:
    started = NOW - age
    return RestoreRecord(
        restore_id=restore_id,
        status="completed",
        started_at=started,
        finished_at=started + timedelta(minutes=1),
        repository_id=repository_id,
        target="/restauraciones/aislada",
        snapshot_id=snapshot_id,
        inventory_verified=inventory_verified,
        mismatches=0 if inventory_verified else 1,
    )


def restore_test(
    test_id: str = "t1",
    *,
    age: timedelta = timedelta(days=10),
    restore_id: str = "rs1",
    snapshot_id: str = SNAPSHOT_ID,
    repository_id: str = REPOSITORY_ID,
) -> RestoreTest:
    performed = NOW - age
    reviewed = performed + timedelta(hours=1)
    return RestoreTest.model_validate(
        {
            "test_id": test_id,
            "system": "bk-diario",
            "performed_at": performed,
            "restore_id": restore_id,
            "isolated_target": True,
            "backup": {"snapshot_id": snapshot_id},
            "result": {"restored": True, "integrity_verified": True, "application_started": True},
            "review": {
                "approved": True,
                "reviewer": "Ana",
                "role": "security_officer",
                "reviewed_at": reviewed,
                "valid_until": reviewed + timedelta(days=365),
            },
            "repository_id": repository_id,
        }
    )


def healthy_records() -> dict[str, list]:
    return {
        "runs": [backup_run()],
        "checks": [integrity_check()],
        "restores": [restore_record()],
        "restore_tests": [restore_test()],
    }


def restic_run(
    *, offsite: bool = True, unreadable: dict | None = None, **records: list
) -> ResticRun:
    """Configuración de la recogida con los registros dados (por defecto, ninguno)."""
    return ResticRun(
        **CONFIG,
        offsite=OFFSITE if offsite else None,
        records=WorkspaceRecords(**records, unreadable=unreadable or {}),
    )
