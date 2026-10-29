"""Copia independiente de las evidencias con restic (ADR 0008)."""

from ens_evidence.backup.environment import (
    ACCESS_KEY_VAR,
    PASSWORD_VAR,
    SECRET_KEY_VAR,
    BackupSecrets,
    load_secrets,
    subprocess_env,
)
from ens_evidence.backup.errors import BackupError
from ens_evidence.backup.immutability import parse_s3_repository, verify_immutability
from ens_evidence.backup.integrity import check_scope, run_check
from ens_evidence.backup.records import (
    IMMUTABILITY_PREFIX,
    BackupRun,
    ImmutabilityCheck,
    IntegrityCheck,
    RestoreRecord,
    dump,
    list_checks,
    list_restores,
    list_runs,
)
from ens_evidence.backup.restic import CheckOutcome, Restic
from ens_evidence.backup.restore_test import (
    RestoreTest,
    RestoreTestDeclaration,
    record_restore_test,
)
from ens_evidence.backup.runner import (
    BackupStatus,
    RestoreReport,
    backup_status,
    restore_recorded,
    restore_snapshot,
    run_backup,
)
from ens_evidence.backup.setup_failure import (
    SetupFailureRecorder,
    failed_check_recorder,
    failed_restore_recorder,
    failed_run_recorder,
)

__all__ = [
    "ACCESS_KEY_VAR",
    "IMMUTABILITY_PREFIX",
    "PASSWORD_VAR",
    "SECRET_KEY_VAR",
    "BackupError",
    "BackupRun",
    "BackupSecrets",
    "BackupStatus",
    "CheckOutcome",
    "ImmutabilityCheck",
    "IntegrityCheck",
    "RestoreRecord",
    "RestoreTest",
    "RestoreTestDeclaration",
    "RestoreReport",
    "Restic",
    "SetupFailureRecorder",
    "backup_status",
    "check_scope",
    "dump",
    "failed_check_recorder",
    "failed_restore_recorder",
    "failed_run_recorder",
    "list_checks",
    "list_restores",
    "list_runs",
    "load_secrets",
    "parse_s3_repository",
    "record_restore_test",
    "restore_recorded",
    "restore_snapshot",
    "run_backup",
    "run_check",
    "subprocess_env",
    "verify_immutability",
]
