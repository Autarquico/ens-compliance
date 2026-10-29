"""Ejecución, estado y restauración de la copia."""

import secrets as randomness
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pydantic import AwareDatetime, TypeAdapter, ValidationError

from ens_evidence.backup.environment import BackupSecrets, redact
from ens_evidence.backup.errors import BackupError
from ens_evidence.backup.records import (
    SNAPSHOT_MARKER_KEY,
    BackupRun,
    RestoreRecord,
    SnapshotInventory,
    SnapshotMarker,
    dump,
    list_runs,
    read_snapshot_inventory,
    restore_key,
    run_key,
    snapshot_inventory_key,
)
from ens_evidence.backup.restic import Restic
from ens_evidence.backup.transfer import export_all
from ens_evidence.evidence.layout import BACKUP_META_PREFIX, sha256_hex
from ens_evidence.workspace import LocalStore, WorkspaceError, WorkspaceStore

Clock = Callable[[], datetime]


def inventory_of(store: WorkspaceStore) -> dict[str, str]:
    return {key: sha256_hex(store.read_bytes(key)) for key in store.list("")}


def run_backup(
    store: WorkspaceStore,
    restic: Restic,
    repository: str,
    secrets: BackupSecrets,
    clock: Clock,
    repository_id: str | None = None,
) -> BackupRun:
    """Registra started, hace la copia y registra completed o failed."""
    run_id = f"BK-{clock():%Y%m%dT%H%M%SZ}-{randomness.token_hex(3)}"
    started = BackupRun(
        run_id=run_id,
        status="started",
        started_at=clock(),
        repository=repository,
        repository_id=repository_id,
    )
    store.create_bytes(run_key(run_id), dump(started))
    try:
        final = _copy(store, restic, started, clock)
    except (WorkspaceError, OSError) as exc:
        reason = type(exc).__name__ if isinstance(exc, OSError) else redact(str(exc), secrets)
        final = started.model_copy(
            update={
                "status": "failed",
                "finished_at": clock(),
                "error": reason or "error sin detalle",
            }
        )
    try:
        store.write_bytes(run_key(run_id), dump(BackupRun.model_validate(final.model_dump())))
    except (WorkspaceError, OSError) as exc:
        snapshot = f" El snapshot {final.snapshot_id} puede existir." if final.snapshot_id else ""
        raise BackupError(
            f"no se pudo escribir el registro final de {run_id} ({type(exc).__name__}); "
            f"la ejecución queda como interrumpida.{snapshot}"
        ) from None
    return final


def _copy(store: WorkspaceStore, restic: Restic, started: BackupRun, clock: Clock) -> BackupRun:
    if store.list(BACKUP_META_PREFIX):
        raise BackupError(
            f"el workspace contiene claves bajo {BACKUP_META_PREFIX}/, espacio reservado de la "
            "copia; no se hace la copia porque su verificación no sería fiable"
        )
    with tempfile.TemporaryDirectory(prefix="ens-backup-") as tmp:
        export = LocalStore(Path(tmp))
        export_all(store, export, exclude={run_key(started.run_id)})
        inventory = inventory_of(export)
        export.create_bytes(
            snapshot_inventory_key(started.run_id),
            SnapshotInventory(
                run_id=started.run_id,
                started_at=started.started_at,
                exported_at=clock(),
                repository=started.repository,
                inventory=inventory,
            )
            .model_dump_json(indent=2)
            .encode(),
        )
        snapshot_id = restic.backup(Path(tmp))
    finished_at = clock()
    return started.model_copy(
        update={
            "status": "completed",
            "finished_at": finished_at,
            "snapshot_id": snapshot_id,
            "inventory": inventory,
        }
    )


@dataclass(frozen=True)
class BackupStatus:
    last: BackupRun | None
    interrupted: list[BackupRun]
    unreadable: list[str]


def backup_status(store: WorkspaceStore) -> BackupStatus:
    runs, unreadable = list_runs(store)
    return BackupStatus(
        last=runs[-1] if runs else None,
        interrupted=[run for run in runs if run.status == "started"],
        unreadable=unreadable,
    )


@dataclass(frozen=True)
class RestoreReport:
    snapshot_id: str
    files: int
    inventory_checked: bool
    mismatches: list[str]


def require_empty_target(target: Path) -> None:
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise BackupError("el destino de la restauración debe ser una carpeta vacía o no existir")


def restore_snapshot(
    restic: Restic, snapshot: str, target: Path, recorded: list[BackupRun]
) -> RestoreReport:
    """Restaura en `target` (vacío) y compara con el inventario que viaja en el snapshot.

    Sin ese inventario la restauración queda sin verificar. Si el workspace tiene el registro
    de la ejecución, también debe coincidir con el del snapshot.
    """
    require_empty_target(target)
    resolved = restic.resolve_snapshot(snapshot)
    snapshot_id = str(resolved["id"])
    restic.restore(resolved, target)
    restored_store = LocalStore(target)
    (target / SNAPSHOT_MARKER_KEY).unlink(missing_ok=True)
    restored = {
        key: digest
        for key, digest in inventory_of(restored_store).items()
        if not key.startswith(f"{BACKUP_META_PREFIX}/")
    }
    meta = read_snapshot_inventory(restored_store)
    if meta is None:
        return RestoreReport(snapshot_id, len(restored), False, [])
    expected = meta.inventory
    keys = sorted(set(expected) | set(restored))
    mismatches = [key for key in keys if expected.get(key) != restored.get(key)]
    for run in recorded:
        if run.run_id == meta.run_id and run.status == "completed" and run.inventory != expected:
            mismatches.append(f"registro {run.run_id}: el inventario del workspace no coincide")
    if not mismatches:
        marker = SnapshotMarker(
            snapshot_id=snapshot_id, run_id=meta.run_id, snapshot_time=_snapshot_time(resolved)
        )
        restored_store.write_bytes(SNAPSHOT_MARKER_KEY, marker.model_dump_json().encode())
    return RestoreReport(snapshot_id, len(restored), True, mismatches)


def _snapshot_time(resolved: dict[str, object]) -> datetime | None:
    try:
        return TypeAdapter(AwareDatetime).validate_python(resolved.get("time"))
    except ValidationError:
        return None


def restore_recorded(
    store: WorkspaceStore | None,
    restic: Restic,
    snapshot: str,
    target: Path,
    recorded: list[BackupRun],
    clock: Clock,
    repository_id: str | None = None,
) -> RestoreReport:
    """`restore_snapshot` y, si hay workspace, su registro started -> completed o failed."""
    require_empty_target(target)
    restore_id = f"RS-{clock():%Y%m%dT%H%M%SZ}-{randomness.token_hex(3)}"
    started = RestoreRecord(
        restore_id=restore_id,
        status="started",
        started_at=clock(),
        repository_id=repository_id,
        target=str(target.resolve()),
    )
    if store is not None:
        store.create_bytes(restore_key(restore_id), dump(started))
    try:
        report = restore_snapshot(restic, snapshot, target, recorded)
    except (WorkspaceError, OSError):
        _write_restore(
            store, restore_id, started, {"status": "failed", "failure": "restore_failed"}, clock
        )
        raise
    _write_restore(
        store,
        restore_id,
        started,
        {
            "status": "completed",
            "snapshot_id": report.snapshot_id,
            "inventory_verified": report.inventory_checked and not report.mismatches,
            "mismatches": len(report.mismatches),
        },
        clock,
    )
    return report


def _write_restore(
    store: WorkspaceStore | None,
    restore_id: str,
    started: RestoreRecord,
    update: dict[str, object],
    clock: Clock,
) -> None:
    if store is None:
        return
    final = RestoreRecord.model_validate(
        started.model_copy(update={"finished_at": clock(), **update}).model_dump()
    )
    try:
        store.write_bytes(restore_key(restore_id), dump(final))
    except (WorkspaceError, OSError) as exc:
        raise BackupError(
            f"no se pudo escribir el registro final de {restore_id} ({type(exc).__name__}); "
            "la restauración queda como interrumpida en el registro"
        ) from None
