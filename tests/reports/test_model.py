import pytest
from pydantic import ValidationError

from ens_evidence.assessment.render import NO_CONFORMITY
from ens_evidence.reports.model import (
    Coverage,
    CycleStage,
    DailyReport,
    MonthlyReport,
    Sourced,
    SourceRef,
    Unavailable,
    render_json,
)

from .helpers import NOON, SHA, daily, monthly, ref


def test_source_ref_requires_known_kind_and_real_sha():
    with pytest.raises(ValidationError):
        SourceRef(kind="rumor", key="k", sha256=SHA, at=NOON)
    with pytest.raises(ValidationError):
        SourceRef(kind="evidence", key="k", sha256="zz", at=NOON)
    with pytest.raises(ValidationError):
        SourceRef(kind="evidence", key="k", sha256=SHA, at=NOON.replace(tzinfo=None))


def test_a_value_without_sources_is_rejected():
    with pytest.raises(ValidationError):
        Sourced[int](value=1, sources=[])


def test_unavailable_needs_a_reason():
    with pytest.raises(ValidationError):
        Unavailable(reason="")


def test_unavailable_is_not_confused_with_a_sourced_value():
    report = DailyReport.model_validate(daily().model_dump(mode="json"))
    assert isinstance(report.assets, Unavailable)
    assert isinstance(report.checks, Sourced)


def test_a_bare_zero_is_not_a_valid_section():
    document = daily().model_dump(mode="json")
    document["checks"] = {"counts": {"PASS": 0}}
    with pytest.raises(ValidationError):
        DailyReport.model_validate(document)


def test_report_requires_the_no_conformity_notice():
    with pytest.raises(ValidationError):
        daily(notices=[])
    assert NO_CONFORMITY in daily().notices


def test_period_must_be_a_real_date():
    with pytest.raises(ValidationError):
        daily(period="2026-02-30")
    with pytest.raises(ValidationError):
        monthly(period="2026-13")


def test_extra_fields_are_rejected():
    document = daily().model_dump(mode="json") | {"compliant": True}
    with pytest.raises(ValidationError):
        DailyReport.model_validate(document)


def test_no_field_claims_compliance():
    names = set(DailyReport.model_fields) | set(MonthlyReport.model_fields)
    assert not {n for n in names if "complian" in n or "cumpl" in n or n == "conformity"}


def test_coverage_partial_follows_the_days():
    assert Coverage.of(30, 31).partial
    assert not Coverage.of(31, 31).partial
    with pytest.raises(ValidationError):
        Coverage(days_with_report=30, days_in_period=31, partial=False)
    with pytest.raises(ValidationError):
        Coverage(days_with_report=32, days_in_period=31, partial=True)


def test_monthly_sections_are_all_present():
    assert (
        len(
            [
                n
                for n, f in MonthlyReport.model_fields.items()
                if n
                not in {
                    "schema_version",
                    "generated_at",
                    "product_version",
                    "notices",
                    "kind",
                    "period",
                    "cycle",
                }
            ]
        )
        == 12
    )


def test_cycle_stage_rejects_unknown_stage_and_bad_run_id():
    with pytest.raises(ValidationError):
        CycleStage(run_id="r1", stage="done", at=NOON, period="2026-11-01")
    with pytest.raises(ValidationError):
        CycleStage(run_id="../x", stage="started", at=NOON, period="2026-11-01")


def test_render_is_deterministic_and_round_trips():
    first, second = render_json(daily()), render_json(daily())
    assert first == second
    assert DailyReport.model_validate_json(first) == daily()
    assert first.endswith(b"\n")


def test_source_in_section_round_trips():
    assert ref().key in render_json(daily()).decode()
