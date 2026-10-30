"""Regla de preparación: función pura (ADR 0002)."""

from enum import StrEnum
from typing import Self

from pydantic import AwareDatetime, Field, field_validator, model_validator

from ens_evidence.models.contracts import Review, Strict
from ens_evidence.models.enums import Applicability, Category, MeasureAssessment, Readiness


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
    collected_at: AwareDatetime | None = None


class Compensation(Strict):
    """Medidas compensatorias que sustituyen una exigida por el anexo II (art. 28.3)."""

    measures: list[str] = Field(
        min_length=1, description="Medidas compensatorias, descritas una a una."
    )
    replaces: list[str] = Field(
        min_length=1,
        description=(
            "Obligaciones de la medida que sustituyen: `base` y/o ids de refuerzo (R1...). "
            "Citar un refuerzo de un grupo alternativo sustituye la elección de ese grupo."
        ),
    )
    justification: str = Field(
        description="Por qué protegen igual o mejor frente al riesgo que la medida sustituida."
    )

    @field_validator("justification")
    @classmethod
    def _justified(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("la justificación de la compensación no puede estar vacía")
        return value

    @field_validator("measures")
    @classmethod
    def _described(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError("cada medida compensatoria necesita una descripción")
        return value


class SelectionBasis(Strict):
    """Fundamento con el que se decidió la aplicabilidad; si cambia, la decisión se revisa."""

    category: Category
    catalog_snapshot_sha256: str
    required_by: str | None = Field(
        default=None, description="Por qué el anexo II la exige; None si no la exigía."
    )
    required_reinforcements: list[str] = Field(default_factory=list)
    alternatives: list[list[str]] = Field(
        default_factory=list, description="Grupos de refuerzos: basta uno de cada grupo."
    )


_GENERIC_REASONS = frozenset({"n/a", "na", "no aplica", "no", "ninguno", "ninguna", "-", "x"})
_MIN_ABSENT_REASON = 20


class AbsentAssets(Strict):
    """Qué tipo de activo no existe en el sistema y por qué (anexo II, 2.1.a)."""

    asset_type: str
    justification: str

    @field_validator("justification")
    @classmethod
    def _specific(cls, value: str) -> str:
        text = value.strip()
        if len(text) < _MIN_ABSENT_REASON or text.rstrip(".").lower() in _GENERIC_REASONS:
            raise ValueError(
                f"debe explicar por qué no existe el activo (al menos {_MIN_ABSENT_REASON} "
                "caracteres, sin fórmulas como «n/a» o «no aplica»)"
            )
        return text


class Measure(Strict):
    id: str
    applicability: Applicability
    justification: str | None = None
    compensation: Compensation | None = None
    absent_assets: AbsentAssets | None = Field(
        default=None,
        description=(
            "Tipo de activo que no existe en el sistema; única vía para dejar NOT_APPLICABLE "
            "una medida que el anexo II exige (anexo II, 2.1.a). Solo las medidas de "
            "`applicability/absent_asset_types.yaml`."
        ),
    )
    chosen_reinforcements: list[str] = Field(
        default_factory=list,
        description="Refuerzo que la organización adopta de cada grupo alternativo.",
    )
    proposed: bool = Field(
        default=False,
        description="La decisión la escribió `applicability propose` y nadie la ha editado.",
    )
    selection: SelectionBasis | None = None
    assessment: MeasureAssessment = MeasureAssessment.NOT_ASSESSED
    review: Review | None = None
    evidence: list[EvidenceRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def _compensation_replaces_an_applicable_measure(self) -> Self:
        if self.compensation and self.applicability is not Applicability.APPLICABLE:
            raise ValueError("la compensación solo cabe en una medida APPLICABLE")
        return self

    @model_validator(mode="after")
    def _absent_assets_exclude_a_measure(self) -> Self:
        if self.absent_assets is None:
            return self
        if self.applicability is not Applicability.NOT_APPLICABLE:
            raise ValueError("absent_assets solo cabe en una medida NOT_APPLICABLE")
        return self

    @model_validator(mode="after")
    def _proposal_is_an_applicable_measure(self) -> Self:
        if self.proposed and (
            self.applicability is not Applicability.APPLICABLE or self.compensation
        ):
            raise ValueError("proposed solo cabe en una medida APPLICABLE sin compensación")
        return self


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
    future = [e for e in measure.evidence if e.collected_at is not None and e.collected_at > now]
    counted = [e for e in measure.evidence if e not in future]
    if not counted:
        details = [f"evidence_in_future:{e.id}" for e in future] or [""]
        return [
            Blocker(kind=BlockerKind.MISSING_EVIDENCE, measure_id=measure.id, detail=detail)
            for detail in details
        ]
    return [
        Blocker(kind=BlockerKind.EXPIRED_EVIDENCE, measure_id=measure.id, detail=evidence.id)
        for evidence in counted
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
