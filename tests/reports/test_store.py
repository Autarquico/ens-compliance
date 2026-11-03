from datetime import UTC, datetime

import pytest

from ens_evidence.reports import store as reports
from ens_evidence.reports.model import CycleStage, Unavailable, render_json
from ens_evidence.reports.store import (
    canonical_previous_daily,
    cycle_finished,
    interrupted_in,
    published,
    reports_in_period,
    run_ids,
    save_html,
    save_pair,
    scan_cycles,
    stages_of,
    write_stage,
)
from ens_evidence.workspace import AlreadyExists, LocalStore

from .helpers import NOON, cycle_info, daily, monthly, put_daily, put_manual_daily


@pytest.fixture
def store(tmp_path):
    return LocalStore(tmp_path / "ws")


def at(day: int, hour: int = 6) -> datetime:
    return datetime(2026, 11, day, hour, tzinfo=UTC)


def test_pair_is_written_under_the_period_prefix(store):
    pair = save_pair(store, "daily", "2026-11-01", b"{}", b"<p/>", at=NOON)
    assert pair.json_key.startswith("evidence/reports/daily/2026-11-01/")
    assert store.read_bytes(pair.html_key) == b"<p/>"
    save_pair(store, "monthly", "2026-10", b"{}", b"<p/>", at=NOON)
    assert store.list("evidence/reports/monthly/2026-10")


def test_second_write_with_same_key_fails_and_keeps_the_first(store):
    save_pair(store, "daily", "2026-11-01", b"one", b"<p/>", at=NOON)
    with pytest.raises(AlreadyExists):
        save_pair(store, "daily", "2026-11-01", b"two", b"<p/>", at=NOON)
    [pair] = reports.pairs(store, "daily", "2026-11-01")
    assert store.read_bytes(pair.json_key) == b"one"


@pytest.mark.parametrize("period", ["2026-11-1", "../x", "2026-13-01", "2026-02-30"])
def test_invalid_period_is_rejected(store, period):
    with pytest.raises(ValueError):
        save_pair(store, "daily", period, b"{}", b"<p/>", at=NOON)


def test_monthly_period_is_a_month(store):
    with pytest.raises(ValueError):
        save_pair(store, "monthly", "2026-11-01", b"{}", b"<p/>", at=NOON)


def test_published_needs_json_and_html(store):
    assert not published(store, "daily", "2026-11-01")
    store.create_bytes("evidence/reports/daily/2026-11-01/20261101T060000000000Z.json", b"{}")
    assert not published(store, "daily", "2026-11-01")
    [pair] = reports.pairs(store, "daily", "2026-11-01")
    save_html(store, pair.json_key, b"<p/>")
    assert published(store, "daily", "2026-11-01")


def test_html_without_json_is_not_published(store):
    store.create_bytes("evidence/reports/daily/2026-11-01/20261101T060000000000Z.html", b"<p/>")
    assert not published(store, "daily", "2026-11-01")


def test_save_html_never_overwrites(store):
    pair = save_pair(store, "daily", "2026-11-01", b"{}", b"<p/>", at=NOON)
    with pytest.raises(AlreadyExists):
        save_html(store, pair.json_key, b"other")


# --- base canónica -----------------------------------------------------------------------------


def test_first_report_has_no_base(store):
    result = canonical_previous_daily(store, "2026-11-01")
    assert isinstance(result, Unavailable) and "anterior" in result.reason


def test_base_is_the_previous_verified_report(store):
    put_daily(store, daily("2026-10-31"), at(0 + 1))
    put_daily(store, daily("2026-11-01"), at(1))
    result = canonical_previous_daily(store, "2026-11-01")
    assert not isinstance(result, Unavailable)
    assert result.report.period == "2026-10-31"
    assert len(result.json_sha256) == len(result.html_sha256) == 64


def test_a_gap_uses_the_most_recent_earlier_report_and_says_which(store):
    put_daily(store, daily("2026-10-28"), at(1))
    result = canonical_previous_daily(store, "2026-11-01")
    assert result.report.period == "2026-10-28"


def test_the_period_itself_and_later_ones_are_not_a_base(store):
    put_daily(store, daily("2026-11-01"), at(1))
    put_daily(store, daily("2026-11-02"), at(2))
    assert isinstance(canonical_previous_daily(store, "2026-11-01"), Unavailable)


def test_corrupt_latest_report_is_unavailable_not_skipped(store):
    put_daily(store, daily("2026-10-30"), at(1))
    save_pair(store, "daily", "2026-10-31", b"{no json", b"<p/>", at=at(2))
    result = canonical_previous_daily(store, "2026-11-01")
    assert isinstance(result, Unavailable) and "2026-10-31" in result.reason


def test_report_with_another_period_inside_is_unavailable(store):
    save_pair(store, "daily", "2026-10-31", render_json(daily("2026-10-30")), b"<p/>", at=at(2))
    assert isinstance(canonical_previous_daily(store, "2026-11-01"), Unavailable)


def test_duplicate_keys_in_json_are_rejected(store):
    text = render_json(daily("2026-10-31")).decode().replace('"kind"', '"kind": "daily", "kind"', 1)
    save_pair(store, "daily", "2026-10-31", text.encode(), b"<p/>", at=at(1))
    assert isinstance(canonical_previous_daily(store, "2026-11-01"), Unavailable)


def test_incomplete_latest_pair_is_unavailable(store):
    store.create_bytes(
        "evidence/reports/daily/2026-10-31/20261031T060000000000Z.json",
        render_json(daily("2026-10-31")),
    )
    result = canonical_previous_daily(store, "2026-11-01")
    assert isinstance(result, Unavailable) and "incompleto" in result.reason


def test_several_reports_in_a_day_the_last_stamp_is_canonical(store):
    first = put_daily(store, daily("2026-10-31", product_version="first"), at(1, 6))
    second = put_daily(store, daily("2026-10-31", product_version="second"), at(1, 9))
    result = canonical_previous_daily(store, "2026-11-01")
    assert result.report.product_version == "second"
    assert result.others == (first.json_key,)
    assert result.pair.json_key == second.json_key


# --- informes sin ciclo y ediciones: nunca favorecen a la organización -----------------------


def test_a_manual_report_does_not_fill_a_day_without_a_cycle(store):
    put_manual_daily(store, daily("2026-11-01"), at(1))
    scan = reports_in_period(store, "2026-11")
    assert scan.coverage.days_with_report == 0 and not scan.days
    assert scan.problems and "2026-11-01" in scan.problems[0]


def test_filling_a_whole_month_by_hand_leaves_coverage_at_zero(store):
    for day in range(1, 31):
        put_manual_daily(store, daily(f"2026-11-{day:02d}"), at(day))
    scan = reports_in_period(store, "2026-11")
    assert scan.coverage.days_with_report == 0 and scan.coverage.partial


def test_a_manual_report_is_not_a_base_for_changes(store):
    put_manual_daily(store, daily("2026-10-31"), at(1))
    assert isinstance(canonical_previous_daily(store, "2026-11-01"), Unavailable)


def test_a_later_manual_report_does_not_displace_the_cycle_one(store):
    cycle = put_daily(store, daily("2026-10-31"), at(1, 6))
    manual = put_manual_daily(store, daily("2026-10-31", product_version="manual"), at(1, 9))
    result = canonical_previous_daily(store, "2026-11-01")
    assert result.pair.json_key == cycle.json_key
    assert result.others == (manual.json_key,)


def test_a_json_edited_in_place_is_unavailable_with_the_reason(store):
    pair = put_daily(store, daily("2026-10-31"), at(1))
    original = store.read_text(pair.json_key)
    edited = original.replace('"x"', '"y"')
    assert edited != original
    store.write_bytes(pair.json_key, edited.encode())
    result = canonical_previous_daily(store, "2026-11-01")
    assert isinstance(result, Unavailable) and "se editó" in result.reason
    scan = reports_in_period(store, "2026-10")
    assert scan.coverage.days_with_report == 0 and "se editó" in scan.problems[0]


def test_an_html_edited_in_place_is_unavailable(store):
    pair = put_daily(store, daily("2026-10-31"), at(1))
    store.write_bytes(pair.html_key, b"<html>todo bien</html>")
    result = canonical_previous_daily(store, "2026-11-01")
    assert isinstance(result, Unavailable) and "se editó" in result.reason


def test_a_report_whose_reported_marker_is_missing_is_unavailable(store):
    report = daily("2026-10-31")
    pair = put_manual_daily(
        store, report.model_copy(update={"cycle": cycle_info("2026-10-31", at(1))}), at(1)
    )
    result = canonical_previous_daily(store, "2026-11-01")
    assert isinstance(result, Unavailable) and "reported" in result.reason
    assert pair.json_key in result.reason


# --- listado único de ciclos ------------------------------------------------------------------


def test_scan_cycles_lists_the_store_once_however_many_runs(store, monkeypatch):
    for run in ("r1", "r2", "r3"):
        write_stage(store, run, "started", at=NOON, period="2026-11-01")
    listings = []
    original = store.list
    monkeypatch.setattr(store, "list", lambda prefix: listings.append(prefix) or original(prefix))
    scan = scan_cycles(store)
    assert sorted(scan.runs) == ["r1", "r2", "r3"] and len(listings) == 1


def test_scan_cycles_since_skips_older_runs(store):
    write_stage(store, "20261001T060000-aaaaaa", "started", at=NOON, period="2026-10-01")
    write_stage(store, "20261101T060000-bbbbbb", "started", at=NOON, period="2026-11-01")
    assert list(scan_cycles(store, since="20261015T000000").runs) == ["20261101T060000-bbbbbb"]


def test_interrupted_in_flags_future_and_unreadable_runs_as_unreadable(store):
    write_stage(store, "ok", "started", at=NOON, period="2026-11-01")
    write_stage(store, "future", "started", at=at(5), period="2026-11-01")
    store.create_bytes("evidence/reports/cycles/broken.started.json", b"{")
    interrupted, unreadable = interrupted_in(scan_cycles(store).runs, now=NOON)
    assert [c.run_id for c in interrupted] == ["ok"]
    assert sorted(unreadable) == ["broken", "future"]


# --- periodo -----------------------------------------------------------------------------------


def test_period_coverage_counts_days_with_a_readable_report(store):
    for day in (1, 2, 4):
        put_daily(store, daily(f"2026-11-{day:02d}"), at(day))
    scan = reports_in_period(store, "2026-11")
    assert (scan.coverage.days_with_report, scan.coverage.days_in_period) == (3, 30)
    assert scan.coverage.partial and not scan.problems


def test_period_coverage_is_reported_corrupt_days_are_problems(store):
    put_daily(store, daily("2026-11-01"), at(1))
    save_pair(store, "daily", "2026-11-02", b"nope", b"<p/>", at=at(2))
    scan = reports_in_period(store, "2026-11")
    assert scan.coverage.days_with_report == 1
    assert len(scan.problems) == 1 and "2026-11-02" in scan.problems[0]


def test_full_month_is_not_partial(store):
    for day in range(1, 31):
        put_daily(store, daily(f"2026-11-{day:02d}"), at(1))
    assert not reports_in_period(store, "2026-11").coverage.partial


def test_through_limits_the_expected_days(store):
    put_daily(store, daily("2026-11-01"), at(1))
    scan = reports_in_period(store, "2026-11", through=datetime(2026, 11, 1).date())
    assert scan.coverage.days_in_period == 1 and not scan.coverage.partial


def test_other_months_do_not_count(store):
    put_daily(store, daily("2026-10-31"), at(1))
    assert reports_in_period(store, "2026-11").coverage.days_with_report == 0


# --- etapas ------------------------------------------------------------------------------------


def test_stage_marker_is_append_only(store):
    key = write_stage(store, "r1", "started", at=NOON, period="2026-11-01")
    assert key == "evidence/reports/cycles/r1.started.json"
    with pytest.raises(AlreadyExists):
        write_stage(store, "r1", "started", at=NOON, period="2026-11-01")


def test_stages_come_back_in_cycle_order(store):
    for stage in ("checked", "started", "collected"):
        write_stage(store, "r1", stage, at=NOON, period="2026-11-01")
    reading = stages_of(store, "r1")
    assert [m.stage for m in reading.stages] == ["started", "collected", "checked"]
    assert not reading.problems


def test_cycle_is_finished_only_with_a_final_stage(store):
    for stage in ("started", "collected", "checked", "reported"):
        write_stage(store, "r1", stage, at=NOON, period="2026-11-01")
    assert not cycle_finished(store, "r1")
    write_stage(store, "r1", "backup_failed", at=NOON, period="2026-11-01", reference="k")
    assert cycle_finished(store, "r1")


@pytest.mark.parametrize("final", ["backed_up", "backup_failed", "backup_skipped"])
def test_each_backup_outcome_closes_the_cycle(store, final):
    write_stage(store, "r1", "started", at=NOON, period="2026-11-01")
    write_stage(store, "r1", final, at=NOON, period="2026-11-01")
    assert cycle_finished(store, "r1")


def test_unknown_cycle_is_not_finished(store):
    assert not cycle_finished(store, "nope")
    assert stages_of(store, "nope").stages == []


def test_cycles_do_not_mix_even_with_a_shared_prefix(store):
    write_stage(store, "r1", "started", at=NOON, period="2026-11-01")
    write_stage(store, "r10", "backed_up", at=NOON, period="2026-11-01")
    assert [m.stage for m in stages_of(store, "r1").stages] == ["started"]
    assert not cycle_finished(store, "r1")
    assert run_ids(store) == ["r1", "r10"]


def test_unreadable_marker_is_reported_and_blocks_finished(store):
    write_stage(store, "r1", "started", at=NOON, period="2026-11-01")
    store.create_bytes("evidence/reports/cycles/r1.backed_up.json", b"{broken")
    reading = stages_of(store, "r1")
    assert [m.stage for m in reading.stages] == ["started"]
    assert reading.problems and "backed_up" in reading.problems[0]
    assert not cycle_finished(store, "r1")


def test_marker_that_disagrees_with_its_name_is_a_problem(store):
    marker = render_json(CycleStage(run_id="r2", stage="started", at=NOON, period="2026-11-01"))
    store.create_bytes("evidence/reports/cycles/r1.started.json", marker)
    assert stages_of(store, "r1").problems


def test_unknown_stage_file_is_reported(store):
    store.create_bytes("evidence/reports/cycles/r1.weird.json", b"{}")
    assert stages_of(store, "r1").problems


def test_run_id_cannot_escape_the_prefix(store):
    with pytest.raises(ValueError):
        write_stage(store, "../r1", "started", at=NOON, period="2026-11-01")


# --- determinismo ------------------------------------------------------------------------------


def test_same_inputs_give_identical_bytes_and_keys(tmp_path):
    runs = []
    for name in ("a", "b"):
        ws = LocalStore(tmp_path / name)
        pair = put_daily(ws, daily(), NOON)
        runs.append((pair.json_key, ws.read_bytes(pair.json_key)))
    assert runs[0] == runs[1]


def test_monthly_report_serialises_deterministically():
    assert render_json(monthly()) == render_json(monthly())
