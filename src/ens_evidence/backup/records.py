"""Registros de la copia en `evidence/backups/` (ADR 0008)."""

from typing import Annotated, Literal, Self

import yaml
from pydantic import AwareDatetime, Field, StringConstraints, model_validator

from ens_evidence.evidence.layout import BACKUP_META_PREFIX, BACKUPS_PREFIX
from ens_evidence.models.contracts import SHA256_PATTERN, Strict
from ens_evidence.workspace import NotFound, WorkspaceStore

IMMUTABILITY_PREFIX = f"{BACKUPS_PREFIX}/immutability"
Sha256 = Annotated[str, StringConstraints(pattern=SHA256_PATTERN)]


class BackupRun(Strict):
    run_id: str
    status: Literal["started", "completed", "failed"]
    started_at: AwareDatetime
    finished_at: AwareDatetime | None = None
    repository: str
    snapshot_id: str | None = None
    inventory: dict[str, Sha256] | None = None
    error: str | None = None
    reconstructed_from_snapshot: bool = False
    reconstructed_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def _shape_matches_status(self) -> Self:
        if self.status == "started":
            complete = self.finished_at is None and self.snapshot_id is None and self.error is None
        elif self.status == "completed":
            complete = (
                self.finished_at is not None
                and bool(self.snapshot_id)
                and self.inventory is not None
                and self.error is None
            )
        else:
            complete = self.finished_at is not None and bool(self.error) and not self.snapshot_id
        reconstruction_marked = self.reconstructed_from_snapshot == (
            self.reconstructed_at is not None
        )
        if self.reconstructed_from_snapshot and self.status != "completed":
            reconstruction_marked = False
        if not (complete and reconstruction_marked):
            raise ValueError(f"los campos no corresponden al estado {self.status}")
        return self


class SnapshotInventory(Strict):
    """Inventario que viaja dentro del snapshot; no forma parte de sí mismo."""

    run_id: str
    started_at: AwareDatetime
    exported_at: AwareDatetime
    repository: str
    inventory: dict[str, Sha256]


class SnapshotMarker(Strict):
    """Lo escribe `backup restore` solo si verificó el snapshot; `import --all` se fía de él."""

    snapshot_id: Annotated[str, StringConstraints(min_length=1)]
    run_id: str
    snapshot_time: AwareDatetime | None = None


ImmutabilityStatus = Literal["protected", "unprotected", "inconclusive"]


class CanaryResult(Strict):
    prefix: str
    status: ImmutabilityStatus
    detail: str

    @property
    def protected(self) -> bool:
        return self.status == "protected"


class ImmutabilityCheck(Strict):
    checked_at: AwareDatetime
    repository: str
    results: list[CanaryResult] = Field(min_length=1)
    note: str = (
        "Un rechazo aislado no acredita la política completa; "
        "la retención se verifica a mano contra el bucket real."
    )

    @property
    def status(self) -> ImmutabilityStatus:
        statuses = {result.status for result in self.results}
        if "unprotected" in statuses:
            return "unprotected"
        return "inconclusive" if "inconclusive" in statuses else "protected"

    @property
    def protected(self) -> bool:
        return self.status == "protected"


def run_key(run_id: str) -> str:
    return f"{BACKUPS_PREFIX}/{run_id}.yaml"


def snapshot_inventory_key(run_id: str) -> str:
    return f"{BACKUP_META_PREFIX}/{run_id}.inventory.json"


SNAPSHOT_MARKER_KEY = f"{BACKUP_META_PREFIX}/snapshot.json"


def read_snapshot_inventory(store: WorkspaceStore) -> SnapshotInventory | None:
    """El único inventario del snapshot, o None si falta, sobra o su nombre no cuadra."""
    keys = [k for k in store.list(BACKUP_META_PREFIX) if k.endswith(".inventory.json")]
    if len(keys) != 1:
        return None
    try:
        meta = SnapshotInventory.model_validate_json(store.read_bytes(keys[0]))
    except (ValueError, NotFound):
        return None
    return meta if keys[0] == snapshot_inventory_key(meta.run_id) else None


def read_snapshot_marker(store: WorkspaceStore) -> SnapshotMarker | None:
    try:
        return SnapshotMarker.model_validate_json(store.read_bytes(SNAPSHOT_MARKER_KEY))
    except (ValueError, NotFound):
        return None


def dump(record: Strict) -> bytes:
    return yaml.safe_dump(
        record.model_dump(mode="json"), sort_keys=False, allow_unicode=True
    ).encode()


def list_runs(store: WorkspaceStore) -> tuple[list[BackupRun], list[str]]:
    """Ejecuciones ordenadas por inicio y las claves de registros ilegibles."""
    runs: list[BackupRun] = []
    unreadable: list[str] = []
    for key in store.list(BACKUPS_PREFIX):
        if "/" in key.removeprefix(f"{BACKUPS_PREFIX}/"):
            continue
        try:
            runs.append(BackupRun.model_validate(yaml.safe_load(store.read_bytes(key))))
        except (ValueError, yaml.YAMLError, NotFound):
            unreadable.append(key)
    return sorted(runs, key=lambda run: run.started_at), unreadable
