"""Regla de preparación: función pura (ADR 0002)."""

from enum import StrEnum
from typing import Self

from pydantic import AwareDatetime, Field, model_validator

from ens_evidence.models.contracts import Review, Strict
from ens_evidence.models.enums import Applicability, MeasureAssessment, Readiness


class BlockerKind(StrEnum):
    UNDETERMINED_APPLICABILITY = "UNDETERMINED_APPLICABILITY"
    NOT_APPLICABLE_WITHOUT_JUSTIFICATION = "NOT_APPLICABLE_WITHOUT_JUSTIFICATION"
    MISSING_EVIDENCE = "MISSING_EVIDENCE"
    EXPIRED_EVIDENCE = "EXPIRED_EVIDENCE"
    MISSING_REVIEW = "MISSING_REVIEW"
    EXPIRED_REVIEW = "EXPIRED_REVIEW"
    NOT_COMPLIANT = "NOT_COMPLIANT"
    OPEN_FINDING = "OPEN_FINDING"


class EvidenceRef(Strict):
    id: str
    valid_until: AwareDatetime | None = None


class Measure(Strict):
    id: str
    applicability: Applicability
    justification: str | None = None
    assessment: MeasureAssessment = MeasureAssessment.NOT_ASSESSED
    review: Review | None = None
    evidence: list[EvidenceRef] = Field(default_factory=list)


class OpenFinding(Strict):
    id: str
    description: str = ""


class ReadinessInput(Strict):
    # Sin medidas no hay nada que preparar: evita un READY por omisión.
    measures: list[Measure] = Field(min_length=1)
    open_findings: list[OpenFinding] = Field(default_factory=list)
    judgment_executed: bool
    evaluated_at: AwareDatetime


class Blocker(Strict):
    kind: BlockerKind
    measure_id: str | None = None
    detail: str = ""


class ReadinessResult(Strict):
    readiness: Readiness
    blockers: list[Blocker]
    pending_human_review: bool

    @model_validator(mode="after")
    def _ready_iff_no_blockers(self) -> Self:
        if (self.readiness is Readiness.READY) != (not self.blockers):
            raise ValueError("readiness READY requires zero blockers, and NOT_READY at least one")
        return self


def _review_blockers(measure: Measure, now: AwareDatetime) -> list[Blocker]:
    if measure.review is None:
        return [Blocker(kind=BlockerKind.MISSING_REVIEW, measure_id=measure.id)]
    if measure.review.reviewed_at > now:
        return [
            Blocker(
                kind=BlockerKind.MISSING_REVIEW,
                measure_id=measure.id,
                detail="review_in_future",
            )
        ]
    if measure.review.valid_until <= now:
        return [Blocker(kind=BlockerKind.EXPIRED_REVIEW, measure_id=measure.id)]
    return []


def _evidence_blockers(measure: Measure, now: AwareDatetime) -> list[Blocker]:
    if not measure.evidence:
        return [Blocker(kind=BlockerKind.MISSING_EVIDENCE, measure_id=measure.id)]
    return [
        Blocker(kind=BlockerKind.EXPIRED_EVIDENCE, measure_id=measure.id, detail=evidence.id)
        for evidence in measure.evidence
        if evidence.valid_until is not None and evidence.valid_until <= now
    ]


def _measure_blockers(measure: Measure, now: AwareDatetime) -> list[Blocker]:
    match measure.applicability:
        case Applicability.UNDETERMINED:
            return [Blocker(kind=BlockerKind.UNDETERMINED_APPLICABILITY, measure_id=measure.id)]
        case Applicability.NOT_APPLICABLE:
            missing_justification = not (measure.justification and measure.justification.strip())
            blockers = (
                [
                    Blocker(
                        kind=BlockerKind.NOT_APPLICABLE_WITHOUT_JUSTIFICATION,
                        measure_id=measure.id,
                    )
                ]
                if missing_justification
                else []
            )
            return blockers + _review_blockers(measure, now)
        case Applicability.APPLICABLE:
            if measure.assessment is not MeasureAssessment.COMPLIANT:
                return [
                    Blocker(
                        kind=BlockerKind.NOT_COMPLIANT,
                        measure_id=measure.id,
                        detail=measure.assessment,
                    )
                ]
            return _evidence_blockers(measure, now) + _review_blockers(measure, now)


def evaluate_readiness(data: ReadinessInput) -> ReadinessResult:
    blockers = [
        blocker
        for measure in data.measures
        for blocker in _measure_blockers(measure, data.evaluated_at)
    ]
    blockers += [
        Blocker(kind=BlockerKind.OPEN_FINDING, detail=finding.id) for finding in data.open_findings
    ]
    return ReadinessResult(
        readiness=Readiness.NOT_READY if blockers else Readiness.READY,
        blockers=blockers,
        pending_human_review=not data.judgment_executed,
    )
