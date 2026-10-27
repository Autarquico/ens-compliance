"""Expediente y transiciones con guardas (ADR 0002)."""

from typing import Self

from pydantic import AwareDatetime, Field, field_validator, model_validator

from ens_evidence.models.contracts import SHA256_PATTERN, Strict
from ens_evidence.models.enums import Category, DossierState, Readiness, Role
from ens_evidence.models.readiness import ReadinessResult

ORDER = list(DossierState)
SELF_ASSESSORS = {Role.SYSTEM_ADMINISTRATOR, Role.SYSTEM_ADMINISTRATOR_DELEGATE}


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


def _check_guards(category: Category, history: list[Transition], record: Transition) -> None:
    current = history[-1].to_state if history else DossierState.GENERATED
    if record.from_state is not current or ORDER.index(record.to_state) != ORDER.index(current) + 1:
        raise TransitionError(f"cannot go from {current} to {record.to_state}")

    match record.to_state:
        case DossierState.SELF_ASSESSED:
            if record.role not in SELF_ASSESSORS:
                raise TransitionError("SELF_ASSESSED requires system_administrator or delegate")
        case DossierState.ANALYZED:
            if record.role is not Role.SECURITY_OFFICER:
                raise TransitionError("ANALYZED requires security_officer")
            if not (record.conclusions and record.conclusions.strip()):
                raise TransitionError("ANALYZED requires conclusions")
        case DossierState.DECLARED:
            if category is not Category.BASICA:
                raise TransitionError("DECLARED is only allowed for category BASICA")
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
    )
    _check_guards(dossier.category, dossier.transitions, record)
    return Dossier(
        category=dossier.category,
        state=to_state,
        transitions=[*dossier.transitions, record],
    )
