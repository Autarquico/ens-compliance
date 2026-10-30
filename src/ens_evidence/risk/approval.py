"""Revisión y aprobación anual del análisis de riesgos por el responsable de la seguridad."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

import yaml
from pydantic import ValidationError

from ens_evidence.approvals import (
    Rejected,
    file_hashes,
    is_current,
    one_year_after,
    read_record,
)
from ens_evidence.catalog import load_catalog
from ens_evidence.catalog.controls import current_catalog_version
from ens_evidence.categorization import CategorizationState, categorization_status
from ens_evidence.models.enums import Role
from ens_evidence.risk.models import APPROVAL_KEY, METHODOLOGY_MD_KEY, RISK_FILES, RiskApproval
from ens_evidence.risk.templates import METHODOLOGY_MD
from ens_evidence.risk.validate import validate_risk
from ens_evidence.scope import ScopeState, require_approved_scope, scope_status
from ens_evidence.scope.messages import describe_validation
from ens_evidence.workspace import NotFound, WorkspaceStore


class RiskState(StrEnum):
    NOT_APPROVED = "NOT_APPROVED"
    APPROVED = "APPROVED"
    STALE = "STALE"
    EXPIRED = "EXPIRED"
    INVALID = "INVALID"


@dataclass(frozen=True)
class RiskStatus:
    state: RiskState
    errors: list[str] = field(default_factory=list)
    approval: RiskApproval | None = None


class RiskApprovalRejected(Rejected):
    """Motivos en `errors`."""


def _current_categorization_sha256(store: WorkspaceStore, now: datetime) -> str | None:
    status = categorization_status(store, now)
    if status.state is CategorizationState.CURRENT and status.approval is not None:
        return status.approval.file_sha256
    return None


def _methodology_is_template(store: WorkspaceStore) -> bool:
    try:
        return store.read_bytes(METHODOLOGY_MD_KEY) == METHODOLOGY_MD.encode("utf-8")
    except NotFound:
        return False


def approve_risk(store: WorkspaceStore, by: str, now: datetime) -> RiskApproval:
    """Aprueba el análisis vigente por un año como `security_officer`. `by` es declarado.

    Lanza `ScopeNotApproved` si el alcance no está aprobado ahora.
    """
    scope = require_approved_scope(store)
    analysis, errors = validate_risk(store, now)
    if analysis is None:
        raise RiskApprovalRejected(errors)
    if _methodology_is_template(store):
        raise RiskApprovalRejected(
            [f"{METHODOLOGY_MD_KEY}: sigue siendo la plantilla; documenta tu metodología"]
        )
    try:
        approval = RiskApproval(
            approved_by=by,
            role=Role.SECURITY_OFFICER,
            approved_at=now,
            valid_until=one_year_after(now),
            scope_fingerprint=scope.fingerprint or "",
            catalog_version=current_catalog_version(load_catalog()),
            categorization_sha256=_current_categorization_sha256(store, now),
            files=file_hashes(store, RISK_FILES),
        )
    except ValidationError as exc:
        raise RiskApprovalRejected(describe_validation("aprobación", exc)) from None
    store.write_text(APPROVAL_KEY, yaml.safe_dump(approval.model_dump(mode="json")))
    return approval


def _stale_reasons(store: WorkspaceStore, approval: RiskApproval, now: datetime) -> list[str]:
    reasons = []
    if file_hashes(store, RISK_FILES) != approval.files:
        reasons.append("risk/ cambió desde que se aprobó")
    scope = scope_status(store)
    if scope.state is not ScopeState.APPROVED:
        reasons.append("el alcance no está aprobado ahora; apruébalo (scope approve)")
    elif scope.fingerprint != approval.scope_fingerprint:
        reasons.append("el alcance cambió desde que se aprobó el análisis")
    if current_catalog_version(load_catalog()) != approval.catalog_version:
        reasons.append("la versión del catálogo cambió desde que se aprobó el análisis")
    if _current_categorization_sha256(store, now) != approval.categorization_sha256:
        reasons.append("la categorización vigente cambió desde que se aprobó el análisis")
    return reasons


def risk_status(store: WorkspaceStore, now: datetime) -> RiskStatus:
    approval, errors = read_record(store, APPROVAL_KEY, RiskApproval, "vuelve a aprobar")
    if errors:
        return RiskStatus(RiskState.INVALID, errors)
    if approval is None:
        return RiskStatus(RiskState.NOT_APPROVED)
    reasons = _stale_reasons(store, approval, now)
    if reasons:
        return RiskStatus(RiskState.STALE, reasons, approval)
    if not is_current(approval.approved_at, approval.valid_until, now):
        return RiskStatus(RiskState.EXPIRED, approval=approval)
    return RiskStatus(RiskState.APPROVED, approval=approval)
