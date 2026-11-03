"""Recuperación tras reinicios y reintentos que cruzan días.

Los reinicios simples (después de la hora, diario hecho, días perdidos, mensual pendiente) los
cubre `test_scheduler.py`; aquí está lo que ocurre con el proceso en marcha varios días: el
contador de reintentos de un trabajo agotado se reinicia al día siguiente, el mensual pendiente
se reintenta cada día del mes hasta publicarse y el diario de un día pasado no se rellena.
"""

import datetime as dt
from zoneinfo import ZoneInfo

from ens_evidence.scheduling.config import CycleConfig, MonthlyConfig, ScheduleConfig
from ens_evidence.scheduling.scheduler import JobResult
from scheduling.test_scheduler import build, utc

MADRID = ZoneInfo("Europe/Madrid")
TWICE = ScheduleConfig(cycle=CycleConfig(attempts=2, retry_interval_minutes=30))
ONCE_NO_MONTHLY = ScheduleConfig(
    monthly=MonthlyConfig(day=28), cycle=CycleConfig(attempts=1, retry_interval_minutes=30)
)


def resume(rig, end) -> None:
    rig.stop.clear()
    rig.run_until(end)


def monthly_down(rig) -> dict:
    """Mientras `state["down"]`, cada intento del mensual falla; el diario siempre funciona."""
    state = {"down": True}
    original = rig.executor.run

    def run(job, cfg, trigger, **options):
        if job.kind == "monthly" and state["down"]:
            rig.executor.script.insert(0, JobResult("failed", "report_failed:ValueError"))
        return original(job, cfg, trigger, **options)

    rig.executor.run = run
    return state


def monthly_runs(rig) -> list[tuple[str, dt.date]]:
    return [
        (job.period, ran_at.astimezone(MADRID).date())
        for job, _, ran_at in rig.executor.runs
        if job.kind == "monthly"
    ]


def test_reinicio_antes_de_la_hora_no_ejecuta_y_a_la_hora_dispara_una_sola_vez():
    rig = build(utc(2026, 11, 2, 3, 0), done={("daily", "2026-11-01")})

    rig.run_until(utc(2026, 11, 2, 4, 59))
    assert rig.executor.runs == []

    resume(rig, utc(2026, 11, 2, 8, 0))
    assert rig.executor.periods == [("daily", "2026-11-02")]
    assert rig.executor.runs[0][1] == "scheduled"


def test_tras_tres_dias_perdidos_solo_se_ejecutan_el_diario_de_hoy_y_el_mensual_del_mes():
    rig = build(utc(2026, 12, 9, 10, 0), ScheduleConfig(), done={("daily", "2026-12-05")})

    rig.run_until(utc(2026, 12, 9, 12, 0))

    assert sorted(rig.executor.periods) == [("daily", "2026-12-09"), ("monthly", "2026-12")]


def test_un_mensual_agotado_recupera_sus_intentos_cada_dia_del_mes_hasta_publicarse():
    rig = build(utc(2026, 12, 9, 10, 0), TWICE, done={("daily", "2026-12-09")})
    state = monthly_down(rig)

    rig.run_until(utc(2026, 12, 11, 23, 0))  # tres días locales (9, 10 y 11) con el mensual caído

    days = [dt.date(2026, 12, d) for d in (9, 10, 11)]
    assert monthly_runs(rig) == [("2026-12", day) for day in days for _ in range(2)]
    assert len(rig.events("job_exhausted")) == 3

    state["down"] = False
    resume(rig, utc(2026, 12, 12, 23, 0))
    assert monthly_runs(rig)[6:] == [("2026-12", dt.date(2026, 12, 12))]
    resume(rig, utc(2026, 12, 14, 12, 0))
    assert len(monthly_runs(rig)) == 7  # publicado: no vuelve a ejecutarse


def test_un_mensual_agotado_no_reintenta_otra_vez_el_mismo_dia():
    rig = build(utc(2026, 12, 9, 10, 0), TWICE, done={("daily", "2026-12-09")})
    monthly_down(rig)

    rig.run_until(utc(2026, 12, 9, 22, 0))  # 23:00 en Madrid: aún es el día 9

    assert monthly_runs(rig) == [("2026-12", dt.date(2026, 12, 9))] * 2


def test_el_mensual_de_un_mes_pasado_no_se_reintenta_cuando_empieza_el_siguiente():
    rig = build(utc(2026, 12, 30, 10, 0), TWICE, done={("daily", "2026-12-30")})
    monthly_down(rig)

    rig.run_until(utc(2027, 1, 2, 12, 0))

    periods = {period for period, _ in monthly_runs(rig)}
    assert periods == {"2026-12", "2027-01"}
    last_december = max(day for period, day in monthly_runs(rig) if period == "2026-12")
    assert last_december == dt.date(2026, 12, 31)
    assert ("2027-01", dt.date(2027, 1, 1)) in monthly_runs(rig)


def test_el_diario_de_un_dia_pasado_no_se_rellena_aunque_se_agotara():
    rig = build(utc(2026, 11, 2, 4, 0), ONCE_NO_MONTHLY, script=[JobResult("failed", "x")])

    rig.run_until(utc(2026, 11, 4, 12, 0))

    assert rig.executor.periods == [
        ("daily", "2026-11-02"),
        ("daily", "2026-11-03"),
        ("daily", "2026-11-04"),
    ]


def test_los_reintentos_del_mensual_que_cruzan_la_medianoche_conservan_el_periodo():
    rig = build(utc(2026, 12, 31, 22, 40), TWICE, done={("daily", "2026-12-31")})
    state = monthly_down(rig)
    rig.clock.on_sleep = lambda: state.update(down=False)  # el servicio vuelve tras el primer fallo

    rig.run_until(utc(2027, 1, 1, 4, 0))
    assert [(job.kind, job.period) for job, _, _ in rig.executor.runs] == [
        ("monthly", "2026-12")
    ] * 2
    assert rig.executor.runs[1][2].astimezone(MADRID).date() == dt.date(2027, 1, 1)

    resume(rig, utc(2027, 1, 1, 5, 30))
    assert rig.executor.periods[2:] == [("daily", "2027-01-01"), ("monthly", "2027-01")]
