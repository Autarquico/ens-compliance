"""Intentos que no llegan a restic porque faltan credenciales o ajustes de la copia.

Quedan registrados como `failed`, sin id ni repositorio (el ajuste puede no estar validado) y
con un código saneado: nunca el mensaje del error ni valores o nombres de secretos. Así el
último intento no puede ser el éxito anterior.
"""

import secrets as randomness
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Protocol

from ens_evidence.backup.errors import BackupError
from ens_evidence.backup.records import (
    BackupRun,
    IntegrityCheck,
    RestoreRecord,
    SetupFailure,
    check_key,
    dump,
    list_runs,
    restore_key,
    run_key,
)
from ens_evidence.backup.runner import require_empty_target
from ens_evidence.workspace import WorkspaceStore

Clock = Callable[[], datetime]


class SetupFailureRecorder(Protocol):
    def __call__(self, store: WorkspaceStore, repository: str, failure: SetupFailure) -> None: ...


def failed_run_recorder(clock: Clock) -> SetupFailureRecorder:
    def record(store: WorkspaceStore, repository: str, failure: SetupFailure) -> None:
        now = clock()
        run_id = f"BK-{now:%Y%m%dT%H%M%SZ}-{randomness.token_hex(3)}"
        run = BackupRun(
            run_id=run_id,
            status="failed",
            started_at=now,
            finished_at=now,
            repository="",
            error=failure,
        )
        store.create_bytes(run_key(run_id), dump(run))

    return record


def failed_check_recorder(scope: str, clock: Clock) -> SetupFailureRecorder:
    def record(store: WorkspaceStore, repository: str, failure: SetupFailure) -> None:
        now = clock()
        check_id = f"CK-{now:%Y%m%dT%H%M%SZ}-{randomness.token_hex(3)}"
        check = IntegrityCheck(
            check_id=check_id,
            status="failed",
            started_at=now,
            finished_at=now,
            scope=scope,
            failure=failure,
        )
        store.create_bytes(check_key(check_id), dump(check))

    return record


def failed_restore_recorder(target: Path, clock: Clock) -> SetupFailureRecorder:
    """Solo si el workspace ya tiene copias y el destino es válido, como `restore_recorded`."""

    def record(store: WorkspaceStore, repository: str, failure: SetupFailure) -> None:
        runs, _unreadable = list_runs(store)
        try:
            require_empty_target(target)
        except BackupError:
            return
        if not runs:
            return
        now = clock()
        restore_id = f"RS-{now:%Y%m%dT%H%M%SZ}-{randomness.token_hex(3)}"
        restore = RestoreRecord(
            restore_id=restore_id,
            status="failed",
            started_at=now,
            finished_at=now,
            target=str(target.resolve()),
            failure=failure,
        )
        store.create_bytes(restore_key(restore_id), dump(restore))

    return record
