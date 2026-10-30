from datetime import timedelta

import pytest
from pydantic import ValidationError

from ens_evidence.matrix import (
    ApplicabilityReview,
    CheckLink,
    DocumentLink,
    ManualEvidenceLink,
    MatrixEntry,
    MatrixFile,
    Owner,
)
from ens_evidence.models import MeasureAssessment, Role

from .helpers import (
    EVIDENCE_ID,
    RECORDED,
    SHA,
    applicability_review,
    measure_review,
    obligation,
    review,
)

CATALOG = {"snapshot_sha256": SHA, "consolidation_date": "2022-05-03"}


def entry(**fields):
    return MatrixEntry(**{"id": "org.1", **fields})


class TestLinks:
    def test_kinds_are_discriminated_on_load(self):
        loaded = entry(
            links=[
                {"kind": "evidence", "evidence_id": EVIDENCE_ID, "covers": ["base"]},
                {"kind": "check", "check_id": "github.mfa", "covers": ["R1"]},
                {"kind": "org_document", "document": "politica", "covers": ["org.1.1"]},
            ]
        )
        assert [type(link) for link in loaded.links] == [
            ManualEvidenceLink,
            CheckLink,
            DocumentLink,
        ]

    def test_covers_is_mandatory(self):
        with pytest.raises(ValidationError):
            CheckLink(check_id="github.mfa")

    def test_covers_cannot_be_empty(self):
        with pytest.raises(ValidationError):
            CheckLink(check_id="github.mfa", covers=[])

    @pytest.mark.parametrize("covers", [["base"], ["R2"], ["compensation"], ["org.1.1"]])
    def test_accepts_shortcuts_and_clause_ids(self, covers):
        assert CheckLink(check_id="github.mfa", covers=covers).covers == covers

    @pytest.mark.parametrize("covers", [["todo"], ["r1"], ["Base"], ["org..1"], [""]])
    def test_rejects_unknown_cover_tokens(self, covers):
        with pytest.raises(ValidationError, match="covers no válido"):
            CheckLink(check_id="github.mfa", covers=covers)

    def test_rejects_repeated_cover(self):
        with pytest.raises(ValidationError, match="covers repetido"):
            CheckLink(check_id="github.mfa", covers=["base", "base"])

    def test_rejects_malformed_evidence_id(self):
        with pytest.raises(ValidationError, match="id de evidencia"):
            ManualEvidenceLink(evidence_id="EV-1", covers=["base"])

    def test_rejects_unknown_kind(self):
        with pytest.raises(ValidationError):
            entry(links=[{"kind": "url", "covers": ["base"]}])


class TestEntry:
    def test_all_optional_fields_start_empty(self):
        loaded = entry()
        assert (loaded.owner, loaded.review_every_days, loaded.links) == (None, None, [])

    @pytest.mark.parametrize("days", [1, 90, 365])
    def test_accepts_explicit_period_in_range(self, days):
        assert entry(review_every_days=days).review_every_days == days

    @pytest.mark.parametrize("days", [0, -1, 366, 1000])
    def test_rejects_period_out_of_range(self, days):
        with pytest.raises(ValidationError):
            entry(review_every_days=days)

    def test_rejects_duplicate_links_even_with_different_covers(self):
        with pytest.raises(ValidationError, match="enlaces repetidos"):
            entry(
                links=[
                    CheckLink(check_id="github.mfa", covers=["base"]),
                    CheckLink(check_id="github.mfa", covers=["R1"]),
                ]
            )

    def test_same_target_in_different_kinds_is_not_a_duplicate(self):
        loaded = entry(
            links=[
                CheckLink(check_id="x", covers=["base"]),
                DocumentLink(document="x", covers=["base"]),
            ]
        )
        assert len(loaded.links) == 2

    def test_owner_needs_a_name(self):
        with pytest.raises(ValidationError):
            Owner(name="", role=Role.SYSTEM_OWNER)

    @pytest.mark.parametrize("measure_id", ["ORG.1", "org", "../x", "org/1", ""])
    def test_rejects_ids_that_are_not_measure_ids(self, measure_id):
        with pytest.raises(ValidationError):
            entry(id=measure_id)

    def test_rejects_unknown_fields(self):
        with pytest.raises(ValidationError):
            entry(implemented=True)


class TestMatrixFile:
    def test_rejects_repeated_ids(self):
        with pytest.raises(ValidationError, match="ids de medida repetidos: org.1"):
            MatrixFile(catalog_version=CATALOG, entries=[entry(), entry()])

    def test_carries_the_catalog_version(self):
        matrix = MatrixFile(catalog_version=CATALOG, entries=[entry(), entry(id="org.2")])
        assert matrix.catalog_version.snapshot_sha256 == SHA


class TestMeasureReview:
    @pytest.mark.parametrize(
        "assessment",
        [
            MeasureAssessment.COMPLIANT,
            MeasureAssessment.NON_COMPLIANT,
            MeasureAssessment.REQUIRES_REVIEW,
        ],
    )
    def test_records_the_three_human_assessments(self, assessment):
        assert measure_review(assessment=assessment).assessment is assessment

    def test_not_assessed_is_not_a_review_outcome(self):
        with pytest.raises(ValidationError, match="NOT_ASSESSED"):
            measure_review(assessment=MeasureAssessment.NOT_ASSESSED)

    def test_compliant_without_sources_is_rejected(self):
        with pytest.raises(ValidationError, match="COMPLIANT exige"):
            measure_review(obligations=[obligation(sources=False)])

    def test_compliant_without_obligations_is_rejected(self):
        with pytest.raises(ValidationError, match="COMPLIANT exige"):
            measure_review(obligations=[])

    def test_non_compliant_may_have_no_sources(self):
        record = measure_review(
            assessment=MeasureAssessment.NON_COMPLIANT, obligations=[obligation(sources=False)]
        )
        assert record.assessment is MeasureAssessment.NON_COMPLIANT

    def test_a_single_source_among_obligations_is_enough_for_the_model(self):
        other = obligation().model_copy(update={"obligation": "R1", "evidence": []})
        assert measure_review(obligations=[obligation(), other]).obligations[1].has_sources is False

    def test_rejects_repeated_obligations(self):
        with pytest.raises(ValidationError, match="obligaciones repetidas: base"):
            measure_review(obligations=[obligation(), obligation()])

    @pytest.mark.parametrize("field", ["links_sha256", "measure_context_sha256"])
    def test_fingerprints_must_be_sha256(self, field):
        with pytest.raises(ValidationError):
            measure_review(**{field: "xyz"})

    def test_review_dated_after_its_recording_is_rejected(self):
        with pytest.raises(ValidationError, match="reviewed_at"):
            measure_review(review=review(reviewed_at=RECORDED + timedelta(seconds=1)))

    def test_review_dated_at_its_recording_is_accepted(self):
        assert measure_review(review=review(reviewed_at=RECORDED)).recorded_at == RECORDED

    def test_axis_marks_the_record_kind(self):
        assert measure_review().model_dump()["axis"] == "implementation"


@pytest.mark.parametrize(
    "role",
    [
        Role.SECURITY_OFFICER,
        Role.SYSTEM_OWNER,
        Role.INFORMATION_OWNER,
        Role.SERVICE_OWNER,
    ],
)
class TestRolesNotAllowed:
    def test_measure_review(self, role):
        with pytest.raises(ValidationError, match="system_administrator"):
            measure_review(review=review(role=role))

    def test_applicability_review(self, role):
        with pytest.raises(ValidationError, match="system_administrator"):
            ApplicabilityReview(
                measure_id="org.1",
                review=review(role=role),
                recorded_at=RECORDED,
                measure_context_sha256=SHA,
            )


@pytest.mark.parametrize("role", [Role.SYSTEM_ADMINISTRATOR, Role.SYSTEM_ADMINISTRATOR_DELEGATE])
def test_administrator_and_delegate_may_review(role):
    assert measure_review(review=review(role=role)).review.role is role
    assert applicability_review().review.role is Role.SYSTEM_ADMINISTRATOR


class TestApplicabilityReview:
    def test_has_no_assessment(self):
        assert "assessment" not in applicability_review().model_dump()

    def test_rejects_an_assessment(self):
        with pytest.raises(ValidationError):
            ApplicabilityReview(
                measure_id="org.1",
                review=review(),
                recorded_at=RECORDED,
                measure_context_sha256=SHA,
                assessment="COMPLIANT",
            )

    def test_review_dated_after_its_recording_is_rejected(self):
        with pytest.raises(ValidationError, match="reviewed_at"):
            ApplicabilityReview(
                measure_id="org.1",
                review=review(reviewed_at=RECORDED + timedelta(days=1)),
                recorded_at=RECORDED,
                measure_context_sha256=SHA,
            )
