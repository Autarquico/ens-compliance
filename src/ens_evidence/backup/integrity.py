"""`backup check`: registra cada intento de `restic check`, también el que falla o se interrumpe."""

import re
import secrets as randomness
from collections.abc import Callable
from datetime import datetime

from ens_evidence.backup.errors import BackupError
from ens_evidence.backup.records import IntegrityCheck, check_key, dump
from ens_evidence.backup.restic import Restic
from ens_evidence.workspace import WorkspaceError, WorkspaceStore

Clock = Callable[[], datetime]

_PERCENT = re.compile(r"(100|[1-9][0-9]?)%")


def check_scope(read_data_subset: str | None, read_data: bool) -> str:
    if read_data_subset is not None and read_data:
        raise BackupError("--read-data-subset y --read-data son excluyentes")
    if read_data:
        return "all"
    if read_data_subset is None:
        return "metadata"
    if not _PERCENT.fullmatch(read_data_subset):
        raise BackupError("--read-data-subset admite un porcentaje entero entre 1% y 100%")
    return f"subset:{read_data_subset}"


def run_check(
    store: WorkspaceStore,
    restic: Restic,
    repository_id: str,
    clock: Clock,
    *,
    read_data_subset: str | None = None,
    read_data: bool = False,
) -> IntegrityCheck:
    """Registra started, ejecuta `restic check` y registra completed o failed."""
    scope = check_scope(read_data_subset, read_data)
    check_id = f"CK-{clock():%Y%m%dT%H%M%SZ}-{randomness.token_hex(3)}"
    started = IntegrityCheck(
        check_id=check_id,
        status="started",
        started_at=clock(),
        repository_id=repository_id,
        scope=scope,
    )
    store.create_bytes(check_key(check_id), dump(started))
    final = _finish(started, restic, clock, read_data_subset, read_data)
    try:
        store.write_bytes(check_key(check_id), dump(final))
    except (WorkspaceError, OSError) as exc:
        raise BackupError(
            f"no se pudo escribir el registro final de {check_id} ({type(exc).__name__}); "
            "el intento queda como interrumpido"
        ) from None
    return final


def _finish(
    started: IntegrityCheck,
    restic: Restic,
    clock: Clock,
    read_data_subset: str | None,
    read_data: bool,
) -> IntegrityCheck:
    try:
        outcome = restic.check(read_data_subset, read_data)
    except BackupError:
        return _failed(started, clock, failure="restic_unavailable")
    if outcome.num_errors is None or outcome.broken_packs is None:
        return _failed(started, clock, failure="no_summary", exit_code=outcome.exit_code)
    if outcome.exit_code != 0 or outcome.num_errors or outcome.broken_packs:
        return _failed(
            started,
            clock,
            failure="repository_errors",
            exit_code=outcome.exit_code,
            num_errors=outcome.num_errors,
            broken_packs=outcome.broken_packs,
        )
    return IntegrityCheck.model_validate(
        started.model_copy(
            update={
                "status": "completed",
                "finished_at": clock(),
                "exit_code": 0,
                "num_errors": 0,
                "broken_packs": 0,
            }
        ).model_dump()
    )


def _failed(started: IntegrityCheck, clock: Clock, **measured: object) -> IntegrityCheck:
    return IntegrityCheck.model_validate(
        started.model_copy(
            update={"status": "failed", "finished_at": clock(), **measured}
        ).model_dump()
    )
