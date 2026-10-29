"""`backup restore-test record`: prueba de restauración declarada por una persona."""

import re
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal, Self

import yaml
from pydantic import (
    AwareDatetime,
    BeforeValidator,
    Field,
    StrictBool,
    StringConstraints,
    model_validator,
)

from ens_evidence.backup.errors import BackupError
from ens_evidence.backup.records import RepositoryId, dump, list_restores
from ens_evidence.evidence import import_manual
from ens_evidence.evidence.layout import EVIDENCE_ROOT
from ens_evidence.models.contracts import SHA256_PATTERN, Strict
from ens_evidence.models.enums import Role
from ens_evidence.scope import load_assets
from ens_evidence.workspace import AlreadyExists, WorkspaceError, WorkspaceStore

RESTORE_TESTS_PREFIX = f"{EVIDENCE_ROOT}/restore-tests"
MAX_DAYS_AFTER_RESTORE = 7
_INSTANT = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})")
_INSTANT_HINT = "requiere fecha y hora con zona horaria, p. ej. 2026-10-15T10:00:00+00:00"


def _require_zoned_instant(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError(_INSTANT_HINT)
        return value
    if isinstance(value, str) and _INSTANT.fullmatch(value):
        return value
    raise ValueError(_INSTANT_HINT)


Instant = Annotated[AwareDatetime, BeforeValidator(_require_zoned_instant)]
SafeId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
FullSnapshotId = Annotated[str, StringConstraints(pattern=SHA256_PATTERN)]


class RestoreTestBackup(Strict):
    snapshot_id: FullSnapshotId


class RestoreTestResult(Strict):
    restored: StrictBool
    integrity_verified: StrictBool
    application_started: StrictBool | Literal["not_applicable"]
    application_justification: Text | None = None

    @model_validator(mode="after")
    def _justification_only_when_not_applicable(self) -> Self:
        not_applicable = self.application_started == "not_applicable"
        if not_applicable != (self.application_justification is not None):
            raise ValueError(
                "application_justification es obligatoria con not_applicable y solo con él"
            )
        return self


class RestoreTestReview(Strict):
    approved: StrictBool
    reviewer: Text
    role: Role
    reviewed_at: Instant
    valid_until: Instant


class RestoreTestDeclaration(Strict):
    """Lo que escribe la persona; `repository_id` lo añade la herramienta al registrar."""

    test_id: SafeId
    system: Text
    performed_at: Instant
    restore_id: SafeId
    isolated_target: Literal[True]
    backup: RestoreTestBackup
    result: RestoreTestResult
    review: RestoreTestReview

    @model_validator(mode="after")
    def _dates_in_order(self) -> Self:
        if not self.performed_at <= self.review.reviewed_at:
            raise ValueError("performed_at no puede ser posterior a review.reviewed_at")
        if self.review.valid_until <= self.review.reviewed_at:
            raise ValueError("review.valid_until debe ser posterior a review.reviewed_at")
        return self


class RestoreTest(RestoreTestDeclaration):
    repository_id: RepositoryId = Field(description="Repositorio de la restauración citada.")


def restore_test_key(test_id: str) -> str:
    return f"{RESTORE_TESTS_PREFIX}/{test_id}.yaml"


def record_restore_test(
    store: WorkspaceStore, repository_id: str, data: bytes, now: datetime
) -> RestoreTest:
    """Valida la declaración, la ata a la restauración registrada y la guarda como evidencia manual.

    `ValidationError` si la declaración no vale; `BackupError` si no cuadra con el registro.
    """
    try:
        loaded = yaml.safe_load(data)
    except yaml.YAMLError:
        raise BackupError("el fichero no es YAML válido") from None
    declaration = RestoreTestDeclaration.model_validate(loaded)
    if declaration.review.reviewed_at > now:
        raise BackupError("review.reviewed_at está en el futuro")
    _require_inventoried_system(store, declaration.system)
    _require_tied_to_restore(store, declaration, repository_id)
    record = RestoreTest(**declaration.model_dump(), repository_id=repository_id)
    _write(store, record, now)
    return record


def _require_inventoried_system(store: WorkspaceStore, system: str) -> None:
    assets, _errors = load_assets(store)
    if assets is None:
        raise BackupError("no se puede leer el inventario de activos para validar system")
    if not any(asset.id == system for asset in assets.assets):
        raise BackupError("system debe ser el id de un activo del inventario")


def _require_tied_to_restore(
    store: WorkspaceStore, declaration: RestoreTestDeclaration, repository_id: str
) -> None:
    restores, _unreadable = list_restores(store)
    cited = next((r for r in restores if r.restore_id == declaration.restore_id), None)
    if cited is None or cited.status != "completed":
        raise BackupError("restore_id no corresponde a una restauración completada registrada")
    if cited.snapshot_id != declaration.backup.snapshot_id:
        raise BackupError("backup.snapshot_id no coincide con el snapshot restaurado")
    if cited.repository_id != repository_id:
        raise BackupError("la restauración citada es de otro repositorio")
    if cited.inventory_verified is not True:
        raise BackupError("la restauración citada no verificó su inventario")
    _require_performed_just_after(cited.finished_at, declaration.performed_at)
    _require_not_cited_yet(store, declaration.restore_id)


def performed_after_restore_problem(
    finished_at: datetime | None, performed_at: datetime
) -> str | None:
    """Por qué `performed_at` no vale respecto al fin de la restauración citada, si no vale."""
    if finished_at is None or performed_at < finished_at:
        return "performed_at no puede ser anterior al fin de la restauración citada"
    if performed_at - finished_at > timedelta(days=MAX_DAYS_AFTER_RESTORE):
        return (
            f"performed_at debe estar a menos de {MAX_DAYS_AFTER_RESTORE} días del fin de la "
            "restauración citada; haz una restauración nueva"
        )
    return None


def _require_performed_just_after(finished_at: datetime | None, performed_at: datetime) -> None:
    if problem := performed_after_restore_problem(finished_at, performed_at):
        raise BackupError(problem)


def _require_not_cited_yet(store: WorkspaceStore, restore_id: str) -> None:
    for key in store.list(RESTORE_TESTS_PREFIX):
        try:
            loaded = yaml.safe_load(store.read_bytes(key))
        except (WorkspaceError, yaml.YAMLError):
            continue
        if isinstance(loaded, dict) and loaded.get("restore_id") == restore_id:
            raise BackupError("esa restauración ya la cita otro restore test")


def _write(store: WorkspaceStore, record: RestoreTest, now: datetime) -> None:
    data = dump(record)
    try:
        store.create_bytes(restore_test_key(record.test_id), data)
    except AlreadyExists:
        raise BackupError("ya hay un restore test registrado con ese test_id") from None
    try:
        import_manual(
            store,
            data,
            asset_id=record.system,
            submitted_by=record.review.reviewer,
            now=now.astimezone(UTC),
            collected_at=record.performed_at,
            valid_until=record.review.valid_until,
        )
    except (WorkspaceError, ValueError) as exc:
        raise BackupError(
            f"el restore test se guardó pero no se pudo registrar como evidencia "
            f"({type(exc).__name__}); no se puede repetir con el mismo test_id"
        ) from None
