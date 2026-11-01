"""Los dos cambios de hora de 2026 en Madrid (29-03 y 25-10) con la hora configurable.

Un solo disparo por día local, a la hora correcta en UTC, y un reinicio entre los dos pliegues
(folds) no repite. Todo con `FakeClock`: ninguna prueba duerme.
"""

import datetime as dt

import pytest

from scheduling.test_scheduler import at_time, build, due_times, utc

SPRING, AUTUMN = dt.date(2026, 3, 29), dt.date(2026, 10, 25)
MONTHS_DONE = {("monthly", "2026-03"), ("monthly", "2026-10")}

# (hora configurada, día del cambio, disparo esperado en UTC)
CASES = [
    pytest.param(dt.time(2, 30), SPRING, utc(2026, 3, 29, 1, 30), id="02:30-inexistente-29-03"),
    pytest.param(dt.time(2, 30), AUTUMN, utc(2026, 10, 25, 0, 30), id="02:30-ambigua-25-10"),
    pytest.param(dt.time(6, 0), SPRING, utc(2026, 3, 29, 4, 0), id="06:00-29-03"),
    pytest.param(dt.time(6, 0), AUTUMN, utc(2026, 10, 25, 5, 0), id="06:00-25-10"),
]


def window(day: dt.date) -> tuple[dt.datetime, dt.datetime]:
    """Del mediodía UTC de la víspera a las 23:00 UTC del día: sin disparos de otros días."""
    start = dt.datetime.combine(day - dt.timedelta(days=1), dt.time(12), tzinfo=dt.UTC)
    return start, dt.datetime.combine(day, dt.time(23), tzinfo=dt.UTC)


@pytest.mark.parametrize(("time", "day", "expected"), CASES)
def test_un_solo_disparo_en_el_dia_del_cambio_a_la_hora_correcta_en_utc(time, day, expected):
    start, end = window(day)
    eve = ("daily", (day - dt.timedelta(days=1)).isoformat())
    rig = build(start, at_time(time.hour, time.minute), done=MONTHS_DONE | {eve})

    rig.run_until(end)

    assert rig.executor.periods == [("daily", day.isoformat())]
    assert due_times(rig) == [expected]
    ran_at = rig.executor.runs[0][2]
    assert dt.timedelta(0) <= ran_at - expected < dt.timedelta(seconds=60)


@pytest.mark.parametrize(("time", "day", "expected"), CASES)
def test_un_reinicio_despues_del_disparo_no_repite_en_ninguna_ocurrencia_de_la_hora(
    time, day, expected
):
    start, end = window(day)
    eve = ("daily", (day - dt.timedelta(days=1)).isoformat())
    rig = build(start, at_time(time.hour, time.minute), done=MONTHS_DONE | {eve})
    rig.run_until(end)
    assert len(rig.executor.runs) == 1

    for restart_at in (
        expected,
        expected + dt.timedelta(hours=1),
        expected + dt.timedelta(hours=2),
    ):
        restarted = build(restart_at, at_time(time.hour, time.minute), done=rig.done)
        restarted.run_until(restart_at + dt.timedelta(hours=3))
        assert restarted.executor.runs == [], restart_at


def test_el_25_10_un_reinicio_en_la_segunda_ocurrencia_de_las_02_30_no_repite():
    config = at_time(2, 30)
    first = build(utc(2026, 10, 24, 23), config, done=MONTHS_DONE)
    first.run_until(utc(2026, 10, 25, 1, 0))  # ya pasó la primera ocurrencia (00:30Z)
    assert due_times(first) == [utc(2026, 10, 25, 0, 30)]

    second_fold = utc(2026, 10, 25, 1, 30)  # 02:30 hora de invierno: la misma hora local otra vez
    restarted = build(second_fold, config, done=first.done)
    restarted.run_until(second_fold + dt.timedelta(minutes=10))

    assert restarted.executor.runs == []


def test_el_29_03_la_hora_inexistente_no_dispara_antes_del_salto_desplazado():
    config = at_time(2, 30)
    before_shift = build(utc(2026, 3, 29, 1, 15), config, done=MONTHS_DONE)  # 03:15 de verano

    assert before_shift.scheduler.tick() == []

    at_shift = build(utc(2026, 3, 29, 1, 30), config, done=MONTHS_DONE)  # 03:30: 02:30 + 1 h

    assert [job.period for job, _ in at_shift.scheduler.tick()] == ["2026-03-29"]
