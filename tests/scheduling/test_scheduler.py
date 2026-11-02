"""El planificador: disparos, DST, reintentos, lock, recarga de configuración y parada.

Todo con `FakeClock` y `FakeExecutor`: ninguna prueba duerme. Al final, una integración con el
ciclo real y `scheduler status` / `scheduler run` por la CLI.
"""

import datetime as dt
import os
import signal
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from itertools import pairwise

import pytest
from typer.testing import CliRunner

from ens_evidence.cli import app
from ens_evidence.reports.store import cycle_finished, pairs, run_ids, stages_of, write_stage
from ens_evidence.scheduling import scheduler as scheduler_module
from ens_evidence.scheduling.calendar import Job
from ens_evidence.scheduling.clock import SystemClock
from ens_evidence.scheduling.config import (
    SCHEDULE_KEY,
    CycleConfig,
    DailyConfig,
    LoadedSchedule,
    MonthlyConfig,
    ScheduleConfig,
    ScheduleConfigError,
)
from ens_evidence.scheduling.scheduler import (
    CycleExecutor,
    JobResult,
    Scheduler,
    config_sha256,
    render_status,
    scheduler_status,
    stop_on_signals,
    workspace_done_periods,
)
from ens_evidence.workspace.cycle_lock import CycleLock
from scheduling import test_cycle
from scheduling.fakes import FakeClock, FakeExecutor

# El día mensual cae lejos de los días que se simulan, salvo en las pruebas del día mensual.
CFG = ScheduleConfig(monthly=MonthlyConfig(day=28))


def utc(*parts: int) -> datetime:
    return datetime(*parts, tzinfo=UTC)


def at_time(hour: int, minute: int, **options) -> ScheduleConfig:
    return ScheduleConfig(
        daily=DailyConfig(time=dt.time(hour, minute)), monthly=MonthlyConfig(day=28), **options
    )


@dataclass
class Rig:
    clock: FakeClock
    executor: FakeExecutor
    scheduler: Scheduler
    stop: threading.Event
    done: set
    logs: list[str] = field(default_factory=list)
    config: ScheduleConfig | Exception = field(default_factory=lambda: CFG)

    def run_until(self, end: datetime) -> None:
        self.clock.stop_at(end, self.stop)
        self.scheduler.run()

    def events(self, name: str) -> list[str]:
        return [line for line in self.logs if f"event={name}" in line]


def build(start: datetime, cfg: ScheduleConfig = CFG, script=None, done=None, **options) -> Rig:
    clock = FakeClock(start)
    done = set() if done is None else done
    executor = FakeExecutor(clock, done, script, **options)
    stop = threading.Event()
    rig = Rig(clock, executor, None, stop, done, config=cfg)  # type: ignore[arg-type]

    def load() -> ScheduleConfig:
        if isinstance(rig.config, Exception):
            raise rig.config
        return rig.config

    rig.scheduler = Scheduler(clock, executor, load, lambda: set(done), stop, log=rig.logs.append)
    return rig


def due_times(rig: Rig) -> list[datetime]:
    return [job.due_at for job, _, _ in rig.executor.runs]


# --- disparo diario ----------------------------------------------------------------------------


def test_el_diario_se_dispara_una_vez_al_dia_durante_tres_dias():
    rig = build(utc(2026, 11, 2, 0, 0))

    rig.run_until(utc(2026, 11, 5, 0, 0))

    assert rig.executor.periods == [
        ("daily", "2026-11-02"),
        ("daily", "2026-11-03"),
        ("daily", "2026-11-04"),
    ]
    assert due_times(rig) == [utc(2026, 11, d, 5, 0) for d in (2, 3, 4)]
    for job, trigger, ran_at in rig.executor.runs:
        assert timedelta(0) <= ran_at - job.due_at < timedelta(seconds=60)
        assert trigger == "scheduled"
    assert max(rig.clock.sleeps) <= 60


def test_no_dispara_nada_antes_de_la_hora():
    rig = build(utc(2026, 11, 2, 4, 58))

    assert rig.scheduler.tick() == []
    rig.clock.current = utc(2026, 11, 2, 4, 59, 59)
    assert rig.scheduler.tick() == []
    rig.clock.current = utc(2026, 11, 2, 5, 0, 0)
    assert [job.period for job, _ in rig.scheduler.tick()] == ["2026-11-02"]


def test_el_sueno_nunca_pasa_de_60_segundos_ni_se_pasa_de_la_hora():
    rig = build(utc(2026, 11, 2, 4, 59, 30))

    rig.run_until(utc(2026, 11, 2, 5, 5))

    assert rig.clock.sleeps[0] == 30
    assert all(1 <= seconds <= 60 for seconds in rig.clock.sleeps)
    assert rig.executor.periods == [("daily", "2026-11-02")]


# --- cambios de hora ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        (
            utc(2026, 3, 27, 23, 30),
            utc(2026, 3, 31),
            [utc(2026, 3, 28, 5), utc(2026, 3, 29, 4), utc(2026, 3, 30, 4)],
        ),
        (
            utc(2026, 10, 23, 22),
            utc(2026, 10, 27),
            [utc(2026, 10, 24, 4), utc(2026, 10, 25, 5), utc(2026, 10, 26, 5)],
        ),
    ],
    ids=["29-03", "25-10"],
)
def test_las_06_00_locales_se_mantienen_a_traves_del_cambio_de_hora(start, end, expected):
    rig = build(start, done={("monthly", "2026-03")})

    rig.run_until(end)

    assert due_times(rig) == expected
    assert len({period for _, period in rig.executor.periods}) == 3


def test_hora_inexistente_29_03_se_desplaza_por_el_salto_y_corre_una_vez():
    rig = build(utc(2026, 3, 28, 23), at_time(2, 30), done={("monthly", "2026-03")})

    rig.run_until(utc(2026, 3, 29, 6))

    assert rig.executor.periods == [("daily", "2026-03-29")]
    assert due_times(rig) == [utc(2026, 3, 29, 1, 30)]  # 03:30 hora de verano


def test_hora_ambigua_25_10_usa_la_primera_ocurrencia_y_un_reinicio_entre_folds_no_repite():
    rig = build(utc(2026, 10, 24, 23), at_time(2, 30))
    rig.run_until(utc(2026, 10, 25, 1, 15))
    assert due_times(rig) == [utc(2026, 10, 25, 0, 30)]  # 02:30 de verano
    assert rig.executor.periods == [("daily", "2026-10-25")]

    restarted = build(utc(2026, 10, 25, 1, 45), at_time(2, 30), done=rig.done)  # 02:45 de invierno
    assert restarted.scheduler.tick() == []
    restarted.run_until(utc(2026, 10, 25, 3))
    assert restarted.executor.runs == []


# --- día mensual, reinicio y días perdidos ----------------------------------------------------


def test_el_dia_mensual_ejecuta_el_diario_y_despues_el_mensual_en_serie_en_el_mismo_tick():
    rig = build(utc(2026, 12, 1, 5, 0, 5), ScheduleConfig())

    executed = rig.scheduler.tick()

    assert [(job.kind, job.period) for job, _ in executed] == [
        ("daily", "2026-12-01"),
        ("monthly", "2026-12"),
    ]
    assert rig.executor.max_active == 1
    assert [line.split()[1] for line in rig.logs if "job_" in line] == [
        "event=job_start",
        "event=job_done",
        "event=job_start",
        "event=job_done",
    ]
    assert rig.scheduler.tick() == []


def test_si_el_diario_del_dia_mensual_falla_el_mensual_se_ejecuta_igual_y_el_diario_reintenta():
    rig = build(utc(2026, 12, 1, 5, 0, 5), ScheduleConfig(), script=[JobResult("failed", "x")])

    rig.run_until(utc(2026, 12, 1, 6, 30))

    assert rig.executor.periods == [
        ("daily", "2026-12-01"),
        ("monthly", "2026-12"),
        ("daily", "2026-12-01"),
    ]


def test_reinicio_despues_de_la_hora_sin_el_diario_de_hoy_ejecuta_una_vez():
    rig = build(utc(2026, 11, 2, 10, 0))

    rig.run_until(utc(2026, 11, 2, 12, 0))

    assert rig.executor.periods == [("daily", "2026-11-02")]
    assert rig.executor.runs[0][1] == "recovery"


def test_reinicio_con_el_diario_de_hoy_hecho_no_ejecuta_nada():
    rig = build(utc(2026, 11, 2, 10, 0), done={("daily", "2026-11-02")})

    rig.run_until(utc(2026, 11, 2, 12, 0))

    assert rig.executor.runs == []


def test_los_dias_perdidos_no_se_rellenan():
    rig = build(utc(2026, 11, 5, 10, 0), done=set())  # el equipo estuvo apagado del 2 al 5

    rig.run_until(utc(2026, 11, 5, 12, 0))

    assert rig.executor.periods == [("daily", "2026-11-05")]


def test_el_mensual_pendiente_se_recupera_durante_el_mes():
    rig = build(utc(2026, 12, 9, 10, 0), ScheduleConfig(), done={("daily", "2026-12-09")})

    rig.run_until(utc(2026, 12, 9, 12, 0))

    assert rig.executor.periods == [("monthly", "2026-12")]


# --- reintentos --------------------------------------------------------------------------------


def failed(code: str = "report_failed:ValueError") -> JobResult:
    return JobResult("failed", code)


def test_reintenta_hasta_max_attempts_conservando_el_periodo_y_se_rinde():
    cfg = at_time(6, 0, cycle=CycleConfig(attempts=3, retry_interval_minutes=30))
    rig = build(utc(2026, 11, 2, 4, 0), cfg, script=[failed()] * 5)

    rig.run_until(utc(2026, 11, 2, 12, 0))

    assert rig.executor.periods == [("daily", "2026-11-02")] * 3
    ran = [at for _, _, at in rig.executor.runs]
    assert all(timedelta(minutes=30) <= b - a < timedelta(minutes=31) for a, b in pairwise(ran))
    assert len(rig.events("job_failed")) == 2 and len(rig.events("job_exhausted")) == 1
    assert "attempt=3" in rig.events("job_exhausted")[0]


def test_un_reintento_que_funciona_cierra_el_periodo():
    rig = build(utc(2026, 11, 2, 4, 0), script=[failed(), failed()])

    rig.run_until(utc(2026, 11, 2, 12, 0))

    assert rig.executor.periods == [("daily", "2026-11-02")] * 3
    assert len(rig.events("job_done")) == 1


def test_los_reintentos_cruzan_la_medianoche_con_el_periodo_original():
    rig = build(utc(2026, 11, 2, 22, 0), at_time(23, 50), script=[failed()])  # 22:50Z

    rig.run_until(utc(2026, 11, 3, 4, 0))

    assert rig.executor.periods == [("daily", "2026-11-02")] * 2
    assert rig.executor.runs[1][2] >= utc(2026, 11, 2, 23, 20)  # ya es 3 de noviembre en Madrid


def test_un_trabajo_agotado_no_se_reintenta_hasta_otro_dia():
    cfg = at_time(6, 0, cycle=CycleConfig(attempts=1, retry_interval_minutes=1))
    rig = build(utc(2026, 11, 2, 4, 0), cfg, script=[failed()])

    rig.run_until(utc(2026, 11, 3, 12, 0))

    assert rig.executor.periods == [("daily", "2026-11-02"), ("daily", "2026-11-03")]


def test_una_excepcion_del_ejecutor_cuenta_como_intento_fallido_sin_filtrar_su_mensaje():
    rig = build(utc(2026, 11, 2, 5, 1))
    rig.executor.raises = RuntimeError("token=hunter2-no-debe-salir")

    executed = rig.scheduler.tick()

    assert executed[0][1] == JobResult("failed", "executor_crashed:RuntimeError")
    assert not any("hunter2" in line for line in rig.logs)
    assert "code=executor_crashed:RuntimeError" in rig.events("job_failed")[0]


def test_una_interrupcion_no_cuenta_como_intento():
    cfg = at_time(6, 0, cycle=CycleConfig(attempts=1, retry_interval_minutes=1))
    rig = build(utc(2026, 11, 2, 5, 1), cfg, script=[JobResult("interrupted")])

    first = rig.scheduler.tick()
    second = rig.scheduler.tick()

    assert [r.status for _, r in first] == ["interrupted"]
    assert [r.status for _, r in second] == ["completed"]


# --- lock ocupado ------------------------------------------------------------------------------


def cycle_executor(clock, stop, tmp_path, minutes=1.0) -> CycleExecutor:
    return CycleExecutor(
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        {},
        clock,
        stop,
        lock_wait_minutes=minutes,
        lock_factory=lambda _settings, _env: CycleLock("ws", tmp_path / "state"),
    )


JOB = Job("daily", "2026-11-02", utc(2026, 11, 2, 5, 0))


def test_lock_ocupado_espera_y_el_intento_falla(tmp_path):
    clock, stop = FakeClock(utc(2026, 11, 2, 5, 0)), threading.Event()
    holder = CycleLock("ws", tmp_path / "state").acquire()
    executor = cycle_executor(clock, stop, tmp_path)

    result = executor.run(JOB, CFG, "scheduled")

    assert result == JobResult("busy", "lock_busy")
    assert sum(clock.sleeps) == 60 and max(clock.sleeps) <= 5
    holder.release()


def test_lock_ocupado_cuenta_como_intento_y_el_reintento_lo_encuentra_libre(tmp_path, monkeypatch):
    calls = []

    class Done:
        completed, failure = True, None

    monkeypatch.setattr(scheduler_module, "run_cycle", lambda *a, **k: calls.append(a) or Done())
    clock, stop, done = FakeClock(utc(2026, 11, 2, 5, 0, 1)), threading.Event(), set()
    holder = CycleLock("ws", tmp_path / "state").acquire()
    executor = cycle_executor(clock, stop, tmp_path)
    logs: list[str] = []
    scheduler = Scheduler(clock, executor, lambda: CFG, lambda: done, stop, log=logs.append)

    def release_later() -> None:
        if holder.held and clock.current >= utc(2026, 11, 2, 5, 20):
            holder.release()
        if calls:
            done.add(("daily", "2026-11-02"))
        if clock.current >= utc(2026, 11, 2, 7):
            stop.set()

    clock.on_sleep = release_later
    scheduler.run()

    assert len(calls) == 1
    failed_lines = [line for line in logs if "event=job_failed" in line]
    assert len(failed_lines) == 1 and "code=lock_busy" in failed_lines[0]
    assert any("event=job_done" in line for line in logs)
    CycleLock("ws", tmp_path / "state").acquire().release()  # el ejecutor soltó el lock


def test_parar_durante_la_espera_del_lock_interrumpe_sin_reintentos_rapidos(tmp_path):
    clock, stop = FakeClock(utc(2026, 11, 2, 5, 0)), threading.Event()
    holder = CycleLock("ws", tmp_path / "state").acquire()
    clock.on_sleep = stop.set
    executor = cycle_executor(clock, stop, tmp_path, minutes=10)

    result = executor.run(JOB, CFG, "scheduled")

    assert result.status == "interrupted" and len(clock.sleeps) == 1
    holder.release()


# --- configuración en caliente -----------------------------------------------------------------


def test_la_configuracion_se_recarga_en_cada_tick():
    rig = build(utc(2026, 11, 2, 5, 30), at_time(8, 0))  # 07:00Z
    assert rig.scheduler.tick() == []

    rig.config = at_time(6, 0)  # 05:00Z, ya vencido
    executed = rig.scheduler.tick()

    assert [job.period for job, _ in executed] == ["2026-11-02"]
    assert len(rig.events("config_loaded")) == 2


def test_la_configuracion_invalida_en_caliente_mantiene_la_ultima_valida_y_avisa_una_vez():
    rig = build(utc(2026, 11, 2, 4, 0))
    rig.scheduler.tick()
    sha = config_sha256(CFG)
    assert f"sha256={sha}" in rig.events("config_loaded")[0]

    rig.config = ScheduleConfigError("reports/schedule.yaml: daily.time: valor-secreto-123")
    rig.clock.current = utc(2026, 11, 2, 5, 1)
    executed = rig.scheduler.tick()
    rig.clock.current += timedelta(minutes=1)
    rig.scheduler.tick()

    assert [job.period for job, _ in executed] == ["2026-11-02"]  # sigue con la última válida
    assert rig.scheduler.config_warning
    (warning,) = rig.events("config_invalid")
    assert "error=ScheduleConfigError" in warning and "keeping=last_valid" in warning
    assert not any("valor-secreto" in line for line in rig.logs)

    rig.config = at_time(7, 0)
    rig.scheduler.tick()
    assert not rig.scheduler.config_warning and len(rig.events("config_loaded")) == 2


def test_sin_ninguna_configuracion_valida_no_se_ejecuta_nada():
    rig = build(utc(2026, 11, 2, 5, 1), ScheduleConfigError("x"))  # type: ignore[arg-type]

    assert rig.scheduler.tick() == []
    assert "keeping=none" in rig.events("config_invalid")[0]
    assert rig.executor.runs == []


def test_si_no_se_puede_leer_lo_hecho_no_se_ejecuta_a_ciegas():
    rig = build(utc(2026, 11, 2, 5, 1))

    def unreadable():
        raise OSError("ruta-privada")

    rig.scheduler._done_periods = unreadable

    assert rig.scheduler.tick() == []
    assert "error=OSError" in rig.events("done_unreadable")[0]
    assert not any("ruta-privada" in line for line in rig.logs)


# --- parada y reentrada ------------------------------------------------------------------------


def test_stop_durante_el_sleep_termina_el_bucle_sin_otro_tick():
    rig = build(utc(2026, 11, 2, 4, 0))
    rig.clock.on_sleep = rig.stop.set

    rig.scheduler.run()

    assert len(rig.clock.sleeps) == 1
    assert rig.events("scheduler_stopped") and rig.executor.runs == []


def test_stop_antes_de_empezar_no_hace_ningun_tick():
    rig = build(utc(2026, 11, 2, 5, 1))
    rig.stop.set()

    rig.scheduler.run()

    assert rig.executor.runs == [] and rig.clock.sleeps == []


def test_el_reloj_del_sistema_se_despierta_al_pedir_parar():
    stop = threading.Event()
    clock = SystemClock(stop)
    assert clock.now().utcoffset() == timedelta(0)
    stop.set()

    started = time.monotonic()
    clock.sleep(30)

    assert time.monotonic() - started < 5


def test_sigterm_y_sigint_piden_parar_y_se_restauran_los_manejadores():
    before = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    for sig in (signal.SIGTERM, signal.SIGINT):
        stop = threading.Event()
        with stop_on_signals(stop):
            os.kill(os.getpid(), sig)
            assert stop.wait(5)
    assert {sig: signal.getsignal(sig) for sig in before} == before


def test_nunca_reentra():
    rig = build(utc(2026, 11, 2, 5, 1))
    inner: list = []
    rig.executor.during = lambda: inner.append(rig.scheduler.tick())

    rig.scheduler.tick()

    assert inner == [[]] and len(rig.executor.runs) == 1
    assert rig.executor.max_active == 1
    assert rig.events("tick_skipped")
    assert rig.scheduler.tick() == []  # y vuelve a funcionar después


def test_los_logs_son_una_linea_por_evento_sin_saltos():
    rig = build(utc(2026, 11, 2, 5, 1))
    rig.scheduler.tick()

    assert rig.logs and all("\n" not in line for line in rig.logs)
    assert all(line.split()[1].startswith("event=") for line in rig.logs)


# --- integración con el ciclo real -------------------------------------------------------------


@pytest.fixture
def real(store, settings, env, plugins, fake, restic, tmp_path, monkeypatch):
    collects = []
    original = test_cycle.cycle_module.collect_all
    monkeypatch.setattr(
        test_cycle.cycle_module,
        "collect_all",
        lambda *a, **k: collects.append(a) or original(*a, **k),
    )
    fake("ok")

    def make(start: datetime):
        clock, stop, logs = (
            FakeClock(start, auto_advance=timedelta(seconds=1)),
            threading.Event(),
            [],
        )
        executor = CycleExecutor(
            store,
            settings,
            env,
            clock,
            stop,
            plugins=plugins,
            lock_factory=lambda _s, _e: CycleLock("ws", tmp_path / "lock-state"),
        )
        scheduler = Scheduler(
            clock,
            executor,
            lambda: ScheduleConfig(),
            workspace_done_periods(store, clock),
            stop,
            log=logs.append,
        )
        return scheduler, clock, logs

    make.collects = collects
    return make


def test_en_el_dia_mensual_el_diario_y_luego_el_mensual_sin_repetir_la_recogida(real, store):
    scheduler, clock, logs = real(utc(2026, 12, 1, 5, 0, 30))

    executed = scheduler.tick()

    assert [(job.kind, r.status) for job, r in executed] == [
        ("daily", "completed"),
        ("monthly", "completed"),
    ]
    assert len(real.collects) == 1
    daily_run, monthly_run = (
        next(r for r in run_ids(store) if stages_of(store, r).stages[0].period == period)
        for period in ("2026-12-01", "2026-12")
    )
    assert [m.stage for m in stages_of(store, daily_run).stages] == test_cycle.STAGES
    assert [m.stage for m in stages_of(store, monthly_run).stages] == ["started", "reported"]
    assert pairs(store, "daily", "2026-12-01")[0].published
    assert pairs(store, "monthly", "2026-11")[0].published
    assert cycle_finished(store, daily_run) and cycle_finished(store, monthly_run)
    clock.current += timedelta(minutes=1)
    assert scheduler.tick() == []

    restarted, _, _ = real(clock.current)  # el hecho sale del workspace, no de la memoria
    assert restarted.tick() == [] and len(real.collects) == 1


# --- scheduler status --------------------------------------------------------------------------


def snapshot(store) -> dict[str, bytes]:
    return {key: store.read_bytes(key) for key in store.list("")}


def test_status_muestra_config_proximos_disparos_ultimos_informes_interrumpidos_y_dias_sin_informe(
    real, store, tmp_path
):
    scheduler, _, _ = real(utc(2026, 12, 1, 5, 0, 30))
    scheduler.tick()
    write_stage(
        store, "20261203T050000-abcdef", "started", at=utc(2026, 12, 3, 5), period="2026-12-03"
    )
    before, state_before = snapshot(store), sorted(os.listdir(tmp_path / "lock-state"))
    now = utc(2026, 12, 5, 7, 0)

    status = scheduler_status(store, LoadedSchedule(ScheduleConfig(), is_default=True), now)
    text = render_status(status)

    assert status.sha256 == config_sha256(ScheduleConfig()) and f"sha256: {status.sha256}" in text
    assert "valores por defecto" in text and "zona horaria: Europe/Madrid" in text
    assert "diario: 2026-12-06T06:00:00+01:00" in text
    assert "(pendiente ahora: 2026-12-05, vencido desde 2026-12-05T06:00:00+01:00)" in text
    assert (
        "mensual: 2027-01-01T06:00:00+01:00" in text
        and "mensual: 2027-01-01T06:00:00+01:00 (" not in text
    )
    assert "diario: 2026-12-01" in text and "mensual: 2026-11" in text
    assert (
        "Ciclos interrumpidos: 1" in text and "20261203T050000-abcdef: última etapa started" in text
    )
    assert status.days_without_report == ("2026-12-02", "2026-12-03", "2026-12-04", "2026-12-05")
    assert "Días sin informe diario en 2026-12: 4" in text
    assert "  2026-12-02 a 2026-12-05\n" in text
    assert snapshot(store) == before and sorted(os.listdir(tmp_path / "lock-state")) == state_before


def test_status_antes_de_la_hora_de_hoy_no_cuenta_hoy_como_dia_sin_informe(store):
    status = scheduler_status(
        store, LoadedSchedule(ScheduleConfig(), is_default=True), utc(2026, 12, 2, 4, 0)
    )

    assert status.days_without_report == ("2026-12-01",)
    assert all(last is None for last in status.last_reports.values())
    assert "diario: ninguno" in render_status(status)


def test_status_el_primer_dia_del_mes_antes_de_la_hora_no_lista_dias(store):
    status = scheduler_status(
        store, LoadedSchedule(ScheduleConfig(), is_default=True), utc(2026, 12, 1, 4, 0)
    )

    assert status.days_without_report == ()


@pytest.fixture
def cli(settings_path, monkeypatch):
    monkeypatch.setattr("ens_evidence.cli.clock", lambda: utc(2026, 12, 5, 7, 0))

    def run(*args: str):
        return CliRunner().invoke(app, ["--settings", str(settings_path), *args])

    return run


def test_cli_scheduler_status(cli, store):
    before = snapshot(store)

    result = cli("scheduler", "status")

    assert result.exit_code == 0, result.output
    assert f"sha256: {config_sha256(ScheduleConfig())}" in result.output
    assert "Próximo disparo:" in result.output
    assert snapshot(store) == before


def test_cli_scheduler_status_con_configuracion_invalida_sale_con_2(cli, store):
    store.create_bytes(SCHEDULE_KEY, b"daily:\n  time: 7\n")

    result = cli("scheduler", "status")

    assert result.exit_code == 2


def test_cli_scheduler_run_con_configuracion_invalida_al_arrancar_sale_con_2(cli, store):
    store.create_bytes(SCHEDULE_KEY, b"timezone: Marte/Olimpo\n")

    result = cli("scheduler", "run")

    assert result.exit_code == 2
    assert "scheduler_started" not in result.output
