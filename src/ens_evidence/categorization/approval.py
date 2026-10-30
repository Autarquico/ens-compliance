"""Carga, estado y aprobación de la categorización por el responsable de la seguridad."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

import yaml
from pydantic import AwareDatetime, Field, ValidationError, field_validator, model_validator

from ens_evidence.approvals import (
    Rejected,
    SecurityOfficer,
    file_sha256,
    is_current,
    one_year_after,
    read_record,
)
from ens_evidence.categorization.calculation import Computation, compute
from ens_evidence.categorization.models import CategorizationFile
from ens_evidence.models.contracts import SHA256_PATTERN, Strict
from ens_evidence.models.enums import Category, Role
from ens_evidence.scope import ScopeState, load_inventory, require_approved_scope, scope_status
from ens_evidence.scope.inventory import Inventory
from ens_evidence.scope.messages import SEPARATOR, describe_validation
from ens_evidence.scope.secrets import find_secrets
from ens_evidence.workspace import NotFound, NotText, WorkspaceStore

CATEGORIZATION_KEY = "scope/categorization.yaml"
APPROVAL_KEY = "scope/categorization-approval.yaml"


class CategorizationApproval(Strict):
    file_sha256: str = Field(pattern=SHA256_PATTERN)
    scope_fingerprint: str = Field(pattern=SHA256_PATTERN)
    category: Category
    approved_by: str
    role: SecurityOfficer
    approved_at: AwareDatetime
    valid_until: AwareDatetime

    @field_validator("approved_by")
    @classmethod
    def _declared(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("quien aprueba no puede estar vacío")
        return value

    @model_validator(mode="after")
    def _annual(self) -> "CategorizationApproval":
        if self.valid_until != one_year_after(self.approved_at):
            raise ValueError("valid_until debe ser un año después de approved_at")
        return self


class CategorizationState(StrEnum):
    INVALID = "INVALID"
    PENDING = "PENDING"
    NOT_APPROVED = "NOT_APPROVED"
    STALE = "STALE"
    EXPIRED = "EXPIRED"
    CURRENT = "CURRENT"


@dataclass(frozen=True)
class CategorizationStatus:
    state: CategorizationState
    errors: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    computation: Computation | None = None
    approval: CategorizationApproval | None = None


class CategorizationNotCurrent(Exception):
    def __init__(self, status: CategorizationStatus) -> None:
        super().__init__(f"categorization is {status.state.value}")
        self.status = status


class CategorizationApprovalRejected(Rejected):
    """Motivos en `errors`."""


def _check(categorization: CategorizationFile, inventory: Inventory, now: datetime) -> list[str]:
    errors: list[str] = []
    known = {service.id for service in inventory.services.services}
    included = set(inventory.system.included_services)
    valued = {item.id for item in categorization.services}
    for ref in sorted(valued - known):
        errors.append(
            f"{CATEGORIZATION_KEY}{SEPARATOR}{ref}: el servicio no existe en el inventario"
        )
    for ref in sorted((valued & known) - included):
        errors.append(
            f"{CATEGORIZATION_KEY}{SEPARATOR}{ref}: el servicio está excluido del alcance"
        )
    for ref in sorted(included - valued):
        errors.append(f"{CATEGORIZATION_KEY}: falta valorar el servicio {ref} del alcance")
    for item in (*categorization.information, *categorization.services):
        if item.valuation and item.valuation.valued_at > now:
            errors.append(
                f"{CATEGORIZATION_KEY}{SEPARATOR}{item.id}: valued_at está en el futuro; corrígelo"
            )
    return errors


def _pending(categorization: CategorizationFile) -> list[str]:
    pending = []
    if not categorization.information:
        pending.append(
            "information: no hay informaciones valoradas; el anexo I valora "
            "cada información y cada servicio"
        )
    for kind, items in (
        ("information", categorization.information),
        ("service", categorization.services),
    ):
        for item in items:
            missing = [dimension.value for dimension in item.missing_dimensions]
            if missing:
                pending.append(f"{kind}/{item.id}: faltan las dimensiones {', '.join(missing)}")
            if item.valuation is None:
                pending.append(f"{kind}/{item.id}: falta la valoración de su responsable")
    return pending


def _load(
    store: WorkspaceStore, inventory: Inventory, now: datetime
) -> tuple[CategorizationFile | None, list[str]]:
    try:
        text = store.read_text(CATEGORIZATION_KEY)
    except NotFound:
        return None, [f"{CATEGORIZATION_KEY}: no existe; créalo con las valoraciones"]
    except NotText:
        return None, [f"{CATEGORIZATION_KEY}: no es texto UTF-8; guárdalo como UTF-8"]
    try:
        raw: Any = yaml.safe_load(text)
    except yaml.YAMLError:
        return None, [f"{CATEGORIZATION_KEY}: no es YAML válido; revisa la sintaxis"]
    leaked = find_secrets(raw)
    if leaked:
        return None, [
            f"{CATEGORIZATION_KEY}{SEPARATOR}{p}: parece un secreto; quítalo" for p in leaked
        ]
    try:
        categorization = CategorizationFile.model_validate(raw)
    except ValidationError as exc:
        return None, describe_validation(CATEGORIZATION_KEY, exc, raw)
    errors = _check(categorization, inventory, now)
    return (None if errors else categorization), errors


def _assess_file(store: WorkspaceStore, now: datetime) -> CategorizationStatus:
    """Evalúa el fichero sin mirar la aprobación guardada.

    INVALID o PENDING si no se puede aprobar; NOT_APPROVED si el fichero es aprobable.
    """
    loaded = load_inventory(store)
    if loaded.inventory is None:
        return CategorizationStatus(CategorizationState.INVALID, loaded.errors)
    categorization, errors = _load(store, loaded.inventory, now)
    if categorization is None:
        return CategorizationStatus(CategorizationState.INVALID, errors)
    computation = compute(categorization)
    pending = _pending(categorization)
    if computation.category is None and not pending:
        pending = ["ninguna dimensión tiene nivel: la categoría es indeterminada"]
    if pending:
        return CategorizationStatus(CategorizationState.PENDING, [], pending, computation)
    return CategorizationStatus(CategorizationState.NOT_APPROVED, computation=computation)


def categorization_status(store: WorkspaceStore, now: datetime) -> CategorizationStatus:
    assessed = _assess_file(store, now)
    if assessed.state is not CategorizationState.NOT_APPROVED:
        return assessed
    computation = assessed.computation
    assert computation is not None

    def status(
        state: CategorizationState, errors: list[str], approval=None
    ) -> CategorizationStatus:
        return CategorizationStatus(state, errors, [], computation, approval)

    approval, approval_errors = read_record(
        store, APPROVAL_KEY, CategorizationApproval, "vuelve a aprobar"
    )
    if approval_errors:
        return status(CategorizationState.INVALID, approval_errors)
    if approval is None:
        return status(CategorizationState.NOT_APPROVED, [])
    scope = scope_status(store)
    reasons = []
    if scope.state is not ScopeState.APPROVED:
        reasons.append("el alcance no está aprobado ahora; apruébalo (scope approve)")
    elif approval.scope_fingerprint != scope.fingerprint:
        reasons.append("el alcance cambió desde que se aprobó la categorización")
    if approval.file_sha256 != file_sha256(store, CATEGORIZATION_KEY):
        reasons.append(f"{CATEGORIZATION_KEY} cambió desde que se aprobó")
    if approval.category is not computation.category:
        reasons.append("la categoría calculada difiere de la aprobada")
    if reasons:
        return status(CategorizationState.STALE, reasons, approval)
    if not is_current(approval.approved_at, approval.valid_until, now):
        return status(
            CategorizationState.EXPIRED,
            ["la aprobación está fuera de su año de vigencia"],
            approval,
        )
    return status(CategorizationState.CURRENT, [], approval)


def require_current_categorization(store: WorkspaceStore, now: datetime) -> CategorizationStatus:
    """Categorización aprobada y vigente, o `CategorizationNotCurrent`."""
    status = categorization_status(store, now)
    if status.state is not CategorizationState.CURRENT:
        raise CategorizationNotCurrent(status)
    return status


def approve_categorization(store: WorkspaceStore, by: str, now: datetime) -> CategorizationApproval:
    """Aprueba la categorización como `security_officer`. `by` es declarado, no autenticado.

    Lanza `ScopeNotApproved` si el alcance no está aprobado ahora.
    """
    scope = require_approved_scope(store)
    status = _assess_file(store, now)
    if status.state is CategorizationState.INVALID:
        raise CategorizationApprovalRejected(status.errors)
    if status.state is CategorizationState.PENDING:
        raise CategorizationApprovalRejected(status.pending)
    assert status.computation is not None and status.computation.category is not None
    try:
        approval = CategorizationApproval(
            file_sha256=file_sha256(store, CATEGORIZATION_KEY),
            scope_fingerprint=scope.fingerprint or "",
            category=status.computation.category,
            approved_by=by,
            role=Role.SECURITY_OFFICER,
            approved_at=now,
            valid_until=one_year_after(now),
        )
    except ValidationError as exc:
        raise CategorizationApprovalRejected(describe_validation("aprobación", exc)) from None
    store.write_text(APPROVAL_KEY, yaml.safe_dump(approval.model_dump(mode="json")))
    return approval
