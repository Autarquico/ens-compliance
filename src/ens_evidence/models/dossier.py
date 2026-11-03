"""Expediente y transiciones con guardas (ADR 0002)."""

import hashlib
from typing import Self

from pydantic import AwareDatetime, Field, field_validator, model_validator

from ens_evidence.models.contracts import SHA256_PATTERN, Strict
from ens_evidence.models.enums import Category, DossierState, Readiness, Role
from ens_evidence.models.readiness import ReadinessResult

ORDER = list(DossierState)
SELF_ASSESSORS = {Role.SYSTEM_ADMINISTRATOR, Role.SYSTEM_ADMINISTRATOR_DELEGATE}


def record_sha256(record: "Transition") -> str:
    """Huella del registro completo: la que DECLARED cita como `analysis_sha256`."""
    return hashlib.sha256(record.model_dump_json().encode()).hexdigest()


class TransitionError(ValueError):
    pass


class CorrectiveMeasure(Strict):
    description: str
    addressee: Role

    @field_validator("addressee")
    @classmethod
    def _addressed_to_system_owner(cls, role: Role) -> Role:
        if role is not Role.SYSTEM_OWNER:
            raise ValueError("corrective measures are addressed to system_owner")
        return role


class Transition(Strict):
    from_state: DossierState
    to_state: DossierState
    actor: str
    role: Role
    at: AwareDatetime
    dossier_sha256: str = Field(pattern=SHA256_PATTERN)
    conclusions: str | None = None
    corrective_measures: list[CorrectiveMeasure] = Field(default_factory=list)
    manual_judgment_review: bool = False
    readiness: ReadinessResult | None = None
    elevated_to: str | None = None
    elevated_at: AwareDatetime | None = None
    elevation_confirmed: bool = False
    analysis_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    declaration_evidence_id: str | None = None
    acknowledged_incomplete: list[int] = Field(default_factory=list)


# Campos que solo admite una transición concreta: en cualquier otra se rechazan.
_STATE_FIELDS = {
    "elevated_to": DossierState.ANALYZED,
    "elevated_at": DossierState.ANALYZED,
    "elevation_confirmed": DossierState.ANALYZED,
    "analysis_sha256": DossierState.DECLARED,
    "declaration_evidence_id": DossierState.DECLARED,
    "acknowledged_incomplete": DossierState.DECLARED,
}


def _check_guards(category: Category, history: list[Transition], record: Transition) -> None:
    current = history[-1].to_state if history else DossierState.GENERATED
    if record.from_state is not current or ORDER.index(record.to_state) != ORDER.index(current) + 1:
        raise TransitionError(f"cannot go from {current} to {record.to_state}")

    if history:
        if record.dossier_sha256 != history[0].dossier_sha256:
            raise TransitionError("every transition must carry the same dossier_sha256")
        if record.at < history[-1].at:
            raise TransitionError("transition time must not precede the previous transition")

    misplaced = [
        name
        for name, owner in _STATE_FIELDS.items()
        if owner is not record.to_state and getattr(record, name)
    ]
    if misplaced:
        raise TransitionError(f"{record.to_state} does not take {', '.join(misplaced)}")

    match record.to_state:
        case DossierState.SELF_ASSESSED:
            if record.role not in SELF_ASSESSORS:
                raise TransitionError("SELF_ASSESSED requires system_administrator or delegate")
        case DossierState.ANALYZED:
            if record.role is not Role.SECURITY_OFFICER:
                raise TransitionError("ANALYZED requires security_officer")
            if not (record.conclusions and record.conclusions.strip()):
                raise TransitionError("ANALYZED requires conclusions")
            if (
                not (record.elevated_to and record.elevated_to.strip())
                or record.elevated_at is None
            ):
                raise TransitionError("ANALYZED requires elevated_to and elevated_at")
            if not record.elevation_confirmed:
                raise TransitionError("ANALYZED requires explicit elevation confirmation")
        case DossierState.DECLARED:
            if record.role is not Role.MANAGEMENT:
                raise TransitionError("DECLARED requires management")
            if category is not Category.BASICA:
                raise TransitionError("DECLARED is only allowed for category BASICA")
            analysis = history[-1]
            if record.analysis_sha256 != record_sha256(analysis):
                raise TransitionError("DECLARED analysis_sha256 does not match the ANALYZED record")
            readiness = record.readiness
            if readiness is None or readiness.readiness is not Readiness.READY:
                raise TransitionError("DECLARED requires readiness READY")
            if readiness.pending_human_review and not any(
                t.to_state is DossierState.ANALYZED and t.manual_judgment_review for t in history
            ):
                raise TransitionError(
                    "DECLARED requires manual_judgment_review on the ANALYZED transition "
                    "while pending_human_review is true"
                )


class Dossier(Strict):
    category: Category
    state: DossierState = DossierState.GENERATED
    transitions: list[Transition] = Field(default_factory=list)

    @model_validator(mode="after")
    def _state_follows_valid_history(self) -> Self:
        for index, record in enumerate(self.transitions):
            _check_guards(self.category, self.transitions[:index], record)
        derived = self.transitions[-1].to_state if self.transitions else DossierState.GENERATED
        if self.state is not derived:
            raise ValueError(f"state {self.state} does not match transition history ({derived})")
        return self


def transition(
    dossier: Dossier,
    to_state: DossierState,
    *,
    actor: str,
    role: Role,
    at: AwareDatetime,
    dossier_sha256: str,
    conclusions: str | None = None,
    corrective_measures: list[CorrectiveMeasure] | None = None,
    manual_judgment_review: bool = False,
    readiness: ReadinessResult | None = None,
    elevated_to: str | None = None,
    elevated_at: AwareDatetime | None = None,
    elevation_confirmed: bool = False,
    analysis_sha256: str | None = None,
    declaration_evidence_id: str | None = None,
    acknowledged_incomplete: list[int] | None = None,
) -> Dossier:
    """Devuelve un expediente nuevo con la transición registrada, o lanza TransitionError."""
    record = Transition(
        from_state=dossier.state,
        to_state=to_state,
        actor=actor,
        role=role,
        at=at,
        dossier_sha256=dossier_sha256,
        conclusions=conclusions,
        corrective_measures=corrective_measures or [],
        manual_judgment_review=manual_judgment_review,
        readiness=readiness,
        elevated_to=elevated_to,
        elevated_at=elevated_at,
        elevation_confirmed=elevation_confirmed,
        analysis_sha256=analysis_sha256,
        declaration_evidence_id=declaration_evidence_id,
        acknowledged_incomplete=acknowledged_incomplete or [],
    )
    _check_guards(dossier.category, dossier.transitions, record)
    return Dossier(
        category=dossier.category,
        state=to_state,
        transitions=[*dossier.transitions, record],
    )
