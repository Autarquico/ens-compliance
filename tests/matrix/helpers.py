from datetime import UTC, datetime, timedelta

from applicability.helpers import LATER

from ens_evidence.matrix import (
    ApplicabilityReview,
    MeasureReviewRecord,
    ReviewedClause,
    ReviewedEvidence,
    ReviewedObligation,
)
from ens_evidence.models import MeasureAssessment, Review, Role
from ens_evidence.models.readiness import Compensation

SHA = "a" * 64
OTHER_SHA = "b" * 64
EVIDENCE_ID = "EV-20260601-0a1b2c3d"
SIGNED_AT = LATER + timedelta(hours=1)
COMPENSATION = Compensation(
    replaces=["base"],
    measures=["Segregación de red adicional con monitorización reforzada"],
    justification="Protege igual o mejor frente al riesgo de acceso indebido.",
)
RECORDED = datetime(2026, 6, 10, 12, 0, tzinfo=UTC)


def review(
    role: Role = Role.SYSTEM_ADMINISTRATOR, reviewed_at: datetime = RECORDED - timedelta(days=1)
) -> Review:
    return Review(
        reviewer="Ana",
        role=role,
        reviewed_at=reviewed_at,
        valid_until=reviewed_at + timedelta(days=90),
        conclusion="Revisada",
    )


def obligation(*, sources: bool = True) -> ReviewedObligation:
    evidence = [ReviewedEvidence(evidence_id=EVIDENCE_ID, artifact_sha256=SHA)] if sources else []
    return ReviewedObligation(
        obligation="base", coverage=[ReviewedClause(clause="org.1.1")], evidence=evidence
    )


def measure_review(
    measure_id: str = "org.1",
    recorded_at: datetime = RECORDED,
    assessment: MeasureAssessment = MeasureAssessment.COMPLIANT,
    **overrides,
) -> MeasureReviewRecord:
    fields = {
        "measure_id": measure_id,
        "review": review(reviewed_at=recorded_at - timedelta(hours=1)),
        "recorded_at": recorded_at,
        "assessment": assessment,
        "obligations": [obligation()],
        "links_sha256": SHA,
        "measure_context_sha256": OTHER_SHA,
    }
    return MeasureReviewRecord(**{**fields, **overrides})


def applicability_review(
    measure_id: str = "org.1", recorded_at: datetime = RECORDED
) -> ApplicabilityReview:
    return ApplicabilityReview(
        measure_id=measure_id,
        review=review(reviewed_at=recorded_at - timedelta(hours=1)),
        recorded_at=recorded_at,
        measure_context_sha256=OTHER_SHA,
    )
