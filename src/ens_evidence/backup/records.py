"""Registros de la copia en `evidence/backups/` (ADR 0008)."""

from typing import Annotated, Literal, Self

import yaml
from pydantic import AwareDatetime, Field, StringConstraints, model_validator

from ens_evidence.evidence.layout import BACKUP_META_PREFIX, BACKUPS_PREFIX
from ens_evidence.models.contracts import SHA256_PATTERN, Strict
from ens_evidence.workspace import NotFound, WorkspaceStore

IMMUTABILITY_PREFIX = f"{BACKUPS_PREFIX}/immutability"
CHECKS_PREFIX = f"{BACKUPS_PREFIX}/checks"
RESTORES_PREFIX = f"{BACKUPS_PREFIX}/restores"
Sha256 = Annotated[str, StringConstraints(pattern=SHA256_PATTERN)]
RepositoryId = Annotated[str, StringConstraints(pattern=SHA256_PATTERN)]
CheckScope = Annotated[
    str, StringConstraints(pattern=r"^(metadata|all|subset:(100|[1-9][0-9]?)%)$")
]
RecordStatus = Literal["started", "completed", "failed"]
CheckFailure = Literal["repository_errors", "no_summary", "restic_unavailable"]
RestoreFailure = Literal["restore_failed"]


class BackupRun(Strict):
    run_id: str
    status: Literal["started", "completed", "failed"]
    started_at: AwareDatetime
    finished_at: AwareDatetime | None = None
    repository: str
    repository_id: RepositoryId | None = None
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


class IntegrityCheck(Strict):
    """Un intento de `restic check`. Solo diagnósticos estructurados, nunca la salida de restic."""

    check_id: str
    status: RecordStatus
    started_at: AwareDatetime
    finished_at: AwareDatetime | None = None
    repository_id: RepositoryId | None = None
    scope: CheckScope
    exit_code: int | None = None
    num_errors: int | None = Field(default=None, ge=0)
    broken_packs: int | None = Field(default=None, ge=0)
    failure: CheckFailure | None = None

    @property
    def read_data_percent(self) -> int:
        """Porcentaje de datos de los packs que el intento leyó (0 = solo metadatos)."""
        if self.scope == "metadata":
            return 0
        return 100 if self.scope == "all" else int(self.scope.removeprefix("subset:").rstrip("%"))

    @model_validator(mode="after")
    def _shape_matches_status(self) -> Self:
        measured = (self.exit_code, self.num_errors, self.broken_packs, self.failure)
        if self.status == "started":
            complete = self.finished_at is None and all(v is None for v in measured)
        elif self.status == "completed":
            complete = (
                self.finished_at is not None
                and self.exit_code == 0
                and self.num_errors == 0
                and self.broken_packs == 0
                and self.failure is None
            )
        else:
            complete = self.finished_at is not None and self.failure is not None
        if self.finished_at is not None and self.finished_at < self.started_at:
            complete = False
        if not complete:
            raise ValueError(f"los campos no corresponden al estado {self.status}")
        return self


class RestoreRecord(Strict):
    """Una restauración hecha con `backup restore`; el destino es una carpeta, no un secreto."""

    restore_id: str
    status: RecordStatus
    started_at: AwareDatetime
    finished_at: AwareDatetime | None = None
    repository_id: RepositoryId | None = None
    target: str
    snapshot_id: str | None = None
    inventory_verified: bool | None = None
    mismatches: int | None = Field(default=None, ge=0)
    failure: RestoreFailure | None = None

    @model_validator(mode="after")
    def _shape_matches_status(self) -> Self:
        if self.status == "started":
            complete = self.finished_at is None and self.failure is None
            complete = complete and self.snapshot_id is None
            complete = complete and self.inventory_verified is None and self.mismatches is None
        elif self.status == "completed":
            complete = (
                self.finished_at is not None
                and bool(self.snapshot_id)
                and self.inventory_verified is not None
                and self.mismatches is not None
                and self.failure is None
                and (not self.inventory_verified or self.mismatches == 0)
            )
        else:
            complete = (
                self.finished_at is not None
                and self.failure is not None
                and self.inventory_verified is None
                and self.mismatches is None
            )
        if self.finished_at is not None and self.finished_at < self.started_at:
            complete = False
        if not complete:
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


def check_key(check_id: str) -> str:
    return f"{CHECKS_PREFIX}/{check_id}.yaml"


def restore_key(restore_id: str) -> str:
    return f"{RESTORES_PREFIX}/{restore_id}.yaml"


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
    return _list_records(store, BACKUPS_PREFIX, BackupRun)


def list_checks(store: WorkspaceStore) -> tuple[list[IntegrityCheck], list[str]]:
    return _list_records(store, CHECKS_PREFIX, IntegrityCheck)


def list_restores(store: WorkspaceStore) -> tuple[list[RestoreRecord], list[str]]:
    return _list_records(store, RESTORES_PREFIX, RestoreRecord)


def _list_records[T: BackupRun | IntegrityCheck | RestoreRecord](
    store: WorkspaceStore, prefix: str, model: type[T]
) -> tuple[list[T], list[str]]:
    records: list[T] = []
    unreadable: list[str] = []
    for key in store.list(prefix):
        if "/" in key.removeprefix(f"{prefix}/"):
            continue
        try:
            records.append(model.model_validate(yaml.safe_load(store.read_bytes(key))))
        except (ValueError, yaml.YAMLError, NotFound):
            unreadable.append(key)
    return sorted(records, key=lambda record: record.started_at), unreadable
