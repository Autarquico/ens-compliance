"""Aprobación del alcance por el responsable del sistema y puerta para fases posteriores."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Literal

import yaml
from pydantic import AwareDatetime, Field, ValidationError, field_validator

from ens_evidence.models.contracts import SHA256_PATTERN, Strict
from ens_evidence.models.enums import Role
from ens_evidence.scope.inventory import fingerprint, load_inventory
from ens_evidence.scope.messages import describe_validation
from ens_evidence.workspace import NotFound, NotText, WorkspaceStore

APPROVAL_KEY = "scope/approval.yaml"


class ScopeState(StrEnum):
    NOT_APPROVED = "NOT_APPROVED"
    APPROVED = "APPROVED"
    STALE = "STALE"
    INVALID = "INVALID"


class Approval(Strict):
    fingerprint: str = Field(pattern=SHA256_PATTERN)
    approved_by: str
    role: Role
    approved_at: AwareDatetime

    @field_validator("approved_by")
    @classmethod
    def _declared(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("quien aprueba no puede estar vacío")
        return value

    @field_validator("role")
    @classmethod
    def _system_owner(cls, value: Role) -> Role:
        if value is not Role.SYSTEM_OWNER:
            raise ValueError("solo el responsable del sistema (system_owner) puede aprobar")
        return value


class Revocation(Strict):
    revoked: Literal[True]
    reason: str
    revoked_at: AwareDatetime


@dataclass(frozen=True)
class ScopeStatus:
    state: ScopeState
    errors: list[str] = field(default_factory=list)
    fingerprint: str | None = None
    approval: Approval | None = None


class ScopeNotApproved(Exception):
    def __init__(self, status: ScopeStatus) -> None:
        super().__init__(f"scope is {status.state.value}")
        self.status = status


class ScopeApprovalRejected(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


def scope_status(store: WorkspaceStore) -> ScopeStatus:
    loaded = load_inventory(store)
    if loaded.inventory is None:
        return ScopeStatus(ScopeState.INVALID, loaded.errors)
    current = fingerprint(loaded.inventory)
    try:
        text = store.read_text(APPROVAL_KEY)
    except NotFound:
        return ScopeStatus(ScopeState.NOT_APPROVED, fingerprint=current)
    except NotText:
        return ScopeStatus(
            ScopeState.INVALID, [f"{APPROVAL_KEY}: no es texto UTF-8; vuelve a aprobar"], current
        )
    try:
        raw = yaml.safe_load(text)
        if isinstance(raw, dict) and "revoked" in raw:
            Revocation.model_validate(raw)
            return ScopeStatus(ScopeState.NOT_APPROVED, fingerprint=current)
        approval = Approval.model_validate(raw)
    except yaml.YAMLError:
        detail = [f"{APPROVAL_KEY}: no es YAML válido; vuelve a aprobar"]
        return ScopeStatus(ScopeState.INVALID, detail, current)
    except ValidationError as exc:
        return ScopeStatus(ScopeState.INVALID, describe_validation(APPROVAL_KEY, exc), current)
    state = ScopeState.APPROVED if approval.fingerprint == current else ScopeState.STALE
    return ScopeStatus(state, fingerprint=current, approval=approval)


def approve_scope(store: WorkspaceStore, by: str, role: Role, now: datetime) -> Approval:
    """Aprueba el alcance vigente. `by` es declarado, no autenticado."""
    loaded = load_inventory(store)
    if loaded.inventory is None:
        raise ScopeApprovalRejected(loaded.errors)
    try:
        approval = Approval(
            fingerprint=fingerprint(loaded.inventory), approved_by=by, role=role, approved_at=now
        )
    except ValidationError as exc:
        raise ScopeApprovalRejected(describe_validation("aprobación", exc)) from None
    store.write_text(APPROVAL_KEY, yaml.safe_dump(approval.model_dump(mode="json")))
    return approval


def revoke_approval(store: WorkspaceStore, reason: str, now: datetime) -> bool:
    """Deja constancia de que la aprobación ya no vale; False si no había ninguna."""
    if not store.exists(APPROVAL_KEY):
        return False
    revocation = Revocation(revoked=True, reason=reason, revoked_at=now)
    store.write_text(APPROVAL_KEY, yaml.safe_dump(revocation.model_dump(mode="json")))
    return True


def require_approved_scope(store: WorkspaceStore) -> ScopeStatus:
    status = scope_status(store)
    if status.state is not ScopeState.APPROVED:
        raise ScopeNotApproved(status)
    return status
