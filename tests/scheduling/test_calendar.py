import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from ens_evidence.scheduling.calendar import Job, due_jobs, next_daily, next_monthly
from ens_evidence.scheduling.config import ScheduleConfig

MADRID = ZoneInfo("Europe/Madrid")
UTC = dt.UTC


def cfg(time: str = "06:00", day: int = 1, tz: str = "Europe/Madrid") -> ScheduleConfig:
    return ScheduleConfig.model_validate(
        {"timezone": tz, "daily": {"time": time}, "monthly": {"day": day}}
    )


def daily_jobs(now, config, done):
    return [job for job in due_jobs(now, config, done) if job.kind == "daily"]


def utc(*args: int) -> dt.datetime:
    return dt.datetime(*args, tzinfo=UTC)


def local(*args: int, fold: int = 0) -> dt.datetime:
    return dt.datetime(*args, tzinfo=MADRID, fold=fold)


# --- next_daily ------------------------------------------------------------------------------


def test_default_fires_at_0600_madrid_winter_and_summer():
    assert next_daily(utc(2026, 1, 10, 12), cfg()) == utc(2026, 1, 11, 5)
    assert next_daily(utc(2026, 7, 10, 12), cfg()) == utc(2026, 7, 11, 4)


def test_strictly_after_the_instant_itself():
    due = utc(2026, 1, 11, 5)
    assert next_daily(due, cfg()) == utc(2026, 1, 12, 5)
    assert next_daily(due - dt.timedelta(seconds=1), cfg()) == due


def test_naive_instant_is_rejected():
    with pytest.raises(ValueError):
        next_daily(dt.datetime(2026, 1, 1), cfg())


def test_spring_forward_nonexistent_0230_shifts_by_the_gap():
    # 29-03-2026 02:00 -> 03:00; 02:30 no existe y pasa a 03:30 CEST (01:30Z)
    assert next_daily(utc(2026, 3, 28, 12), cfg("02:30")) == utc(2026, 3, 29, 1, 30)
    # día siguiente: 02:30 CEST normal
    assert next_daily(utc(2026, 3, 29, 2), cfg("02:30")) == utc(2026, 3, 30, 0, 30)


def test_spring_forward_0200_shifts_to_0300():
    due = next_daily(utc(2026, 3, 28, 12), cfg("02:00"))
    assert due == utc(2026, 3, 29, 1, 0)
    assert due.astimezone(MADRID).hour == 3


def test_fall_back_ambiguous_takes_first_occurrence():
    # 25-10-2026 03:00 CEST -> 02:00 CET; 02:30 ocurre dos veces, vale la primera (00:30Z)
    assert next_daily(utc(2026, 10, 24, 12), cfg("02:30")) == utc(2026, 10, 25, 0, 30)
    assert next_daily(utc(2026, 10, 24, 12), cfg("02:00")) == utc(2026, 10, 25, 0, 0)


def test_fall_back_does_not_fire_twice_after_first_occurrence():
    first = utc(2026, 10, 25, 0, 30)
    assert next_daily(first, cfg("02:30")) == utc(2026, 10, 26, 1, 30)


@pytest.mark.parametrize("time", ["00:00", "01:59", "02:00", "02:30", "03:00", "06:00", "23:59"])
@pytest.mark.parametrize("start", [utc(2026, 3, 27), utc(2026, 10, 23), utc(2026, 12, 30)])
def test_property_next_daily_is_after_and_on_configured_local_time(time, start):
    config = cfg(time)
    configured = dt.time.fromisoformat(time)
    now = start
    for _ in range(24 * 6):
        due = next_daily(now, config)
        assert due > now
        shown = due.astimezone(MADRID)
        if shown.time() != configured:
            # solo puede diferir en el hueco primaveral, y entonces por el salto de una hora
            assert shown.date() == dt.date(2026, 3, 29)
            assert shown.time() == (
                dt.datetime.combine(dt.date(2026, 3, 29), configured) + dt.timedelta(hours=1)
            ).time()
        now += dt.timedelta(minutes=37)


@pytest.mark.parametrize("tz", ["UTC", "America/New_York", "Australia/Lord_Howe", "Asia/Kolkata"])
def test_other_zones_keep_local_time(tz):
    config = cfg("06:00", tz=tz)
    now = utc(2026, 1, 1)
    for _ in range(400):
        due = next_daily(now, config)
        assert due > now
        assert due.astimezone(ZoneInfo(tz)).strftime("%H:%M") == "06:00"
        now = due


# --- next_monthly ----------------------------------------------------------------------------


def test_next_monthly_before_and_after_the_day():
    assert next_monthly(utc(2026, 1, 1, 4), cfg()) == utc(2026, 1, 1, 5)
    assert next_monthly(utc(2026, 1, 1, 5), cfg()) == utc(2026, 2, 1, 5)
    assert next_monthly(utc(2026, 12, 15), cfg()) == utc(2027, 1, 1, 5)


def test_next_monthly_day_28_and_dst_month():
    assert next_monthly(utc(2026, 3, 1), cfg(day=28)) == utc(2026, 3, 28, 5)
    assert next_monthly(utc(2026, 3, 1), cfg("02:30", day=29 - 1)) == utc(2026, 3, 28, 1, 30)


# --- due_jobs --------------------------------------------------------------------------------


def test_nothing_before_the_scheduled_time():
    now = local(2026, 5, 12, 5, 59).astimezone(UTC)
    assert daily_jobs(now, cfg(), set()) == []


def test_restart_after_the_time_runs_the_current_period_once():
    now = local(2026, 5, 12, 9, 0).astimezone(UTC)
    [job] = daily_jobs(now, cfg(), set())
    assert job == Job("daily", "2026-05-12", local(2026, 5, 12, 6).astimezone(UTC))
    assert daily_jobs(now, cfg(), {("daily", "2026-05-12")}) == []


def test_past_days_are_never_backfilled():
    now = local(2026, 5, 12, 9, 0).astimezone(UTC)
    jobs = due_jobs(now, cfg(), set())
    assert [j.period for j in jobs if j.kind == "daily"] == ["2026-05-12"]


def test_period_survives_midnight_for_the_job_but_not_for_the_new_day():
    done = set()
    retry_time = local(2026, 5, 13, 0, 10).astimezone(UTC)  # reintento tras medianoche
    # el periodo del ciclo en curso es el de su due_at, que ya no se recalcula
    started = daily_jobs(local(2026, 5, 12, 23, 50).astimezone(UTC), cfg(), done)
    assert [j.period for j in started] == ["2026-05-12"]
    # tras medianoche y antes de las 06:00 no nace un trabajo nuevo
    assert daily_jobs(retry_time, cfg(), {("daily", "2026-05-12")}) == []
    # el job conserva su period aunque se consulte después de medianoche
    assert started[0].period == "2026-05-12"


def test_monthly_fires_after_daily_on_the_day():
    now = local(2026, 6, 1, 6, 0).astimezone(UTC)
    assert [(j.kind, j.period) for j in due_jobs(now, cfg(), set())] == [
        ("daily", "2026-06-01"),
        ("monthly", "2026-06"),
    ]


def test_monthly_recovered_after_day_one_through_the_month():
    for day in (2, 15, 30):
        now = local(2026, 6, day, 9).astimezone(UTC)
        jobs = due_jobs(now, cfg(), {("daily", f"2026-06-{day:02d}")})
        assert [(j.kind, j.period) for j in jobs] == [("monthly", "2026-06")]
    done = {("daily", "2026-06-15"), ("monthly", "2026-06")}
    assert due_jobs(local(2026, 6, 15, 9).astimezone(UTC), cfg(), done) == []


def test_monthly_not_due_before_its_day():
    now = local(2026, 6, 9, 9).astimezone(UTC)
    assert [j.kind for j in due_jobs(now, cfg(day=10), {("daily", "2026-06-09")})] == []


def test_new_month_starts_a_new_monthly_period():
    done = {("monthly", "2026-06")}
    now = local(2026, 7, 1, 7).astimezone(UTC)
    assert ("monthly", "2026-07") in {(j.kind, j.period) for j in due_jobs(now, cfg(), done)}


# --- DST en due_jobs -------------------------------------------------------------------------


def test_restart_between_folds_does_not_repeat_the_job():
    config = cfg("02:30")
    first = utc(2026, 10, 25, 0, 30)
    second = utc(2026, 10, 25, 1, 30)  # misma hora local, segunda ocurrencia
    [job] = daily_jobs(first, config, set())
    assert job.due_at == first and job.period == "2026-10-25"
    done = {("daily", job.period)}
    assert daily_jobs(second, config, done) == []
    # sin registro previo, arrancar en la segunda ocurrencia ejecuta una sola vez
    assert [j.period for j in daily_jobs(second, config, set())] == ["2026-10-25"]


def test_between_folds_before_first_occurrence_nothing_is_due():
    assert daily_jobs(utc(2026, 10, 25, 0, 29), cfg("02:30"), set()) == []


def test_spring_gap_job_fires_at_shifted_time_with_local_period():
    config = cfg("02:30")
    assert daily_jobs(utc(2026, 3, 29, 1, 29), config, set()) == []
    [job] = daily_jobs(utc(2026, 3, 29, 1, 30), config, set())
    assert job.period == "2026-03-29"
    assert job.due_at == utc(2026, 3, 29, 1, 30)


def test_clock_going_backwards_does_not_refire_done_periods():
    config = cfg("06:00")
    done = {("daily", "2026-05-12")}
    assert daily_jobs(local(2026, 5, 12, 6, 30).astimezone(UTC), config, done) == []
    # el reloj retrocede 2 h (antes de la hora): nada, y al volver a pasar de la hora, tampoco
    assert daily_jobs(local(2026, 5, 12, 4, 30).astimezone(UTC), config, done) == []
    assert daily_jobs(local(2026, 5, 12, 6, 31).astimezone(UTC), config, done) == []


def test_clock_going_backwards_over_the_fall_back_hour():
    config = cfg("02:30")
    assert next_daily(utc(2026, 10, 25, 1, 0), config) == utc(2026, 10, 26, 1, 30)
    # tras retroceder, el siguiente disparo sigue siendo posterior y único
    assert next_daily(utc(2026, 10, 25, 0, 10), config) == utc(2026, 10, 25, 0, 30)


def test_round_trip_local_utc_of_every_due_instant():
    config = cfg("02:30")
    now = utc(2026, 3, 20)
    for _ in range(300):
        due = next_daily(now, config)
        assert due.astimezone(MADRID).astimezone(UTC) == due
        now = due
