from datetime import timedelta

import pytest
import yaml

from ens_evidence.matrix import (
    MATRIX_KEY,
    ApplicabilityReview,
    CheckLink,
    MatrixEntry,
    MatrixFile,
    MeasureReviewRecord,
    add_review,
    effective_reviews,
    read_matrix,
    read_reviews,
    save_matrix,
)
from ens_evidence.models import MeasureAssessment
from ens_evidence.scope.sync import SYNCED_KEYS
from ens_evidence.workspace import AlreadyExists

from .helpers import RECORDED, SHA, applicability_review, measure_review, review

CATALOG = {"snapshot_sha256": SHA, "consolidation_date": "2022-05-03"}


def matrix() -> MatrixFile:
    return MatrixFile(
        catalog_version=CATALOG,
        entries=[MatrixEntry(id="org.1", links=[CheckLink(check_id="x.y", covers=["base"])])],
    )


def test_matrix_is_not_synced():
    assert MATRIX_KEY not in SYNCED_KEYS


class TestMatrixFile:
    def test_missing_file_is_not_an_error(self, store):
        assert read_matrix(store) == (None, [])

    def test_roundtrip(self, store):
        save_matrix(store, matrix())
        loaded, errors = read_matrix(store)
        assert (loaded, errors) == (matrix(), [])

    def test_save_replaces_the_previous_version(self, store):
        save_matrix(store, matrix())
        save_matrix(store, MatrixFile(catalog_version=CATALOG, entries=[]))
        loaded, _ = read_matrix(store)
        assert loaded.entries == []

    def test_invalid_yaml_is_reported(self, store):
        store.write_text(MATRIX_KEY, "entries: [")
        loaded, errors = read_matrix(store)
        assert loaded is None and "no es YAML válido" in errors[0]

    def test_validation_errors_are_reported_with_the_key(self, store):
        store.write_text(
            MATRIX_KEY,
            yaml.safe_dump(
                {
                    "catalog_version": CATALOG,
                    "entries": [{"id": "org.1", "review_every_days": 400}],
                }
            ),
        )
        loaded, errors = read_matrix(store)
        assert loaded is None and errors and errors[0].startswith(MATRIX_KEY)

    def test_secrets_are_rejected(self, store):
        store.write_text(
            MATRIX_KEY,
            yaml.safe_dump(
                {
                    "catalog_version": CATALOG,
                    "entries": [{"id": "org.1", "notes": "token", "password": "hunter2hunter2"}],
                }
            ),
        )
        loaded, errors = read_matrix(store)
        assert loaded is None
        assert any("parece un secreto" in e for e in errors)


class TestAppendOnlyReviews:
    def test_stores_under_the_measure_with_a_timestamp_key(self, store):
        key = add_review(store, measure_review())
        assert key == "controls/reviews/org.1/20260610T120000000000Z.yaml"
        assert store.exists(key)

    def test_never_overwrites_an_existing_review(self, store):
        add_review(store, measure_review())
        with pytest.raises(AlreadyExists):
            add_review(store, measure_review(assessment=MeasureAssessment.NON_COMPLIANT))
        (stored,), _ = read_reviews(store, "org.1")
        assert stored.record.assessment is MeasureAssessment.COMPLIANT

    def test_reads_back_both_axes_as_their_own_type(self, store):
        add_review(store, measure_review(recorded_at=RECORDED))
        add_review(store, applicability_review(recorded_at=RECORDED + timedelta(days=1)))
        reviews, errors = read_reviews(store, "org.1")
        assert errors == []
        assert [type(r.record) for r in reviews] == [MeasureReviewRecord, ApplicabilityReview]

    def test_reviews_of_other_measures_are_not_mixed(self, store):
        add_review(store, measure_review("org.1"))
        add_review(store, measure_review("org.2"))
        reviews, _ = read_reviews(store, "org.2")
        assert [r.record.measure_id for r in reviews] == ["org.2"]

    def test_a_review_filed_under_another_measure_is_reported(self, store):
        add_review(store, measure_review("org.2"))
        text = store.read_text("controls/reviews/org.2/20260610T120000000000Z.yaml")
        store.write_text("controls/reviews/org.1/20260610T120000000000Z.yaml", text)
        reviews, errors = read_reviews(store, "org.1")
        assert reviews == [] and "otra medida" in errors[0]

    def test_unreadable_files_are_reported_and_do_not_hide_the_valid_ones(self, store):
        add_review(store, measure_review())
        store.write_text("controls/reviews/org.1/zzz.yaml", "axis: nope\n")
        store.write_text("controls/reviews/org.1/aaa.yaml", "axis: implementation\n")
        reviews, errors = read_reviews(store, "org.1")
        assert len(reviews) == 1
        assert any("zzz.yaml: no es una revisión" in e for e in errors)
        assert any(e.startswith("controls/reviews/org.1/aaa.yaml") for e in errors)

    def test_secrets_in_a_review_are_rejected(self, store):
        add_review(store, measure_review())
        key = "controls/reviews/org.1/20260610T120000000000Z.yaml"
        data = yaml.safe_load(store.read_text(key))
        data["api_key"] = "sk-abcdefghijklmnopqrstuvwxyz0123456789"
        store.write_text(key, yaml.safe_dump(data))
        reviews, errors = read_reviews(store, "org.1")
        assert reviews == [] and any("parece un secreto" in e for e in errors)


class TestTemporalSelection:
    def at(self, days: float):
        return RECORDED + timedelta(days=days)

    def test_no_reviews(self, store):
        found = effective_reviews(store, "org.1", self.at(0))
        assert (found.implementation, found.applicability, found.errors) == (None, None, [])

    def test_picks_the_latest_recorded_up_to_the_query(self, store):
        add_review(
            store,
            measure_review(recorded_at=self.at(0), assessment=MeasureAssessment.NON_COMPLIANT),
        )
        add_review(store, measure_review(recorded_at=self.at(5)))
        found = effective_reviews(store, "org.1", self.at(6))
        assert found.implementation.record.assessment is MeasureAssessment.COMPLIANT

    def test_a_later_review_does_not_exist_yet_at_an_earlier_query(self, store):
        add_review(
            store,
            measure_review(recorded_at=self.at(0), assessment=MeasureAssessment.NON_COMPLIANT),
        )
        add_review(store, measure_review(recorded_at=self.at(5)))
        found = effective_reviews(store, "org.1", self.at(4))
        assert found.implementation.record.assessment is MeasureAssessment.NON_COMPLIANT

    def test_query_before_any_review_finds_none(self, store):
        add_review(store, measure_review(recorded_at=self.at(0)))
        assert effective_reviews(store, "org.1", self.at(-1)).implementation is None

    def test_query_exactly_at_recording_includes_it(self, store):
        add_review(store, measure_review(recorded_at=self.at(0)))
        assert effective_reviews(store, "org.1", self.at(0)).implementation is not None

    def test_selection_uses_recorded_at_not_the_declared_review_date(self, store):
        late_declared = measure_review(
            recorded_at=self.at(1),
            review=review(reviewed_at=self.at(0)),
            assessment=MeasureAssessment.NON_COMPLIANT,
        )
        early_declared = measure_review(
            recorded_at=self.at(2), review=review(reviewed_at=self.at(-30))
        )
        add_review(store, late_declared)
        add_review(store, early_declared)
        found = effective_reviews(store, "org.1", self.at(3))
        assert found.implementation.record.assessment is MeasureAssessment.COMPLIANT

    def test_ties_break_by_key_when_recorded_at_is_equal(self, store):
        first = measure_review(recorded_at=self.at(0), assessment=MeasureAssessment.NON_COMPLIANT)
        second = measure_review(recorded_at=self.at(0), assessment=MeasureAssessment.COMPLIANT)
        store.create_bytes(
            "controls/reviews/org.1/a.yaml",
            yaml.safe_dump(first.model_dump(mode="json")).encode(),
        )
        store.create_bytes(
            "controls/reviews/org.1/b.yaml",
            yaml.safe_dump(second.model_dump(mode="json")).encode(),
        )
        found = effective_reviews(store, "org.1", self.at(1))
        assert found.implementation.key.endswith("b.yaml")
        assert found.implementation.record.assessment is MeasureAssessment.COMPLIANT

    def test_each_axis_has_its_own_latest(self, store):
        add_review(store, measure_review(recorded_at=self.at(0)))
        add_review(store, applicability_review(recorded_at=self.at(1)))
        found = effective_reviews(store, "org.1", self.at(2))
        assert found.implementation.record.recorded_at == self.at(0)
        assert found.applicability.record.recorded_at == self.at(1)

    def test_an_applicability_review_never_replaces_the_assessment(self, store):
        add_review(store, measure_review(recorded_at=self.at(0)))
        add_review(store, applicability_review(recorded_at=self.at(1)))
        found = effective_reviews(store, "org.1", self.at(2))
        assert found.implementation.record.assessment is MeasureAssessment.COMPLIANT
