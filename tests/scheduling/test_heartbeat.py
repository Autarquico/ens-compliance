"""El latido del planificador: lo que lee el healthcheck del contenedor."""

import os
import threading
from datetime import UTC, datetime

from ens_evidence.reports.store import CYCLES_PREFIX
from ens_evidence.scheduling.config import ScheduleConfig
from ens_evidence.scheduling.scheduler import (
    BEAT_EVERY_SECONDS,
    HEARTBEAT_ENV,
    HEARTBEAT_FILE,
    BeatingClock,
    Scheduler,
    file_heartbeat,
)
from scheduling.cycle_helpers import STAGES, TickClock, cycle
from scheduling.fakes import FakeClock, FakeExecutor

START = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


def scheduler(beats: list[str], clock: FakeClock, stop: threading.Event) -> Scheduler:
    return Scheduler(
        clock,
        FakeExecutor(clock, set()),
        lambda: ScheduleConfig(),
        lambda: set(),
        stop,
        log=lambda line: None,
        heartbeat=lambda: beats.append("beat"),
    )


def test_cada_vuelta_del_bucle_late():
    clock, stop = FakeClock(START), threading.Event()
    beats: list[str] = []
    clock.on_sleep = lambda: stop.set() if len(clock.sleeps) >= 3 else None

    scheduler(beats, clock, stop).run()

    assert len(beats) >= 3


def test_sin_latido_configurado_el_planificador_arranca_y_termina():
    clock, stop = FakeClock(START), threading.Event()
    stop.set()

    Scheduler(
        clock,
        FakeExecutor(clock, set()),
        lambda: ScheduleConfig(),
        lambda: set(),
        stop,
        log=lambda line: None,
    ).run()

    assert stop.is_set()


def test_cada_latido_renueva_la_fecha_de_modificacion_del_fichero_de_estado(tmp_path):
    beat = file_heartbeat({"ENS_STATE_DIR": str(tmp_path / "state")})
    heartbeat = tmp_path / "state" / HEARTBEAT_FILE

    beat()
    os.utime(heartbeat, (1, 1))
    beat()

    assert heartbeat.stat().st_mtime > 1_000_000
    assert oct(heartbeat.parent.stat().st_mode & 0o777) == "0o700"


def test_el_latido_va_donde_diga_ens_heartbeat_file_y_no_al_estado(tmp_path):
    elsewhere = tmp_path / "tmp" / "latido"

    file_heartbeat({HEARTBEAT_ENV: str(elsewhere), "ENS_STATE_DIR": str(tmp_path / "state")})()

    assert elsewhere.exists()
    assert not (tmp_path / "state").exists()


def test_si_no_puede_escribir_el_latido_no_rompe_el_planificador(tmp_path):
    blocker = tmp_path / "ocupado"
    blocker.write_text("no es un directorio")

    file_heartbeat({"ENS_STATE_DIR": str(blocker / "state")})()

    assert not (blocker / "state").exists()


class RecordingClock(FakeClock):
    """Anota esperas y latidos en orden para comprobar cuánto se duerme entre latidos."""

    def __init__(self, start: datetime, events: list[tuple[str, float]]) -> None:
        super().__init__(start)
        self.events = events

    def sleep(self, seconds: float) -> None:
        self.events.append(("sleep", seconds))
        super().sleep(seconds)


def beating(events: list[tuple[str, float]]) -> BeatingClock:
    return BeatingClock(RecordingClock(START, events), lambda: events.append(("beat", 0)))


def test_una_espera_larga_no_pasa_mas_de_un_tramo_sin_latir():
    events: list[tuple[str, float]] = []

    beating(events).sleep(150)

    assert sum(seconds for kind, seconds in events if kind == "sleep") == 150
    asleep = 0.0
    for kind, seconds in events:
        asleep = 0.0 if kind == "beat" else asleep + seconds
        assert asleep <= BEAT_EVERY_SECONDS


def test_leer_la_hora_late():
    events: list[tuple[str, float]] = []

    beating(events).now()

    assert ("beat", 0) in events


def test_una_espera_nula_late_igualmente():
    events: list[tuple[str, float]] = []

    beating(events).sleep(0)

    assert ("beat", 0) in events


def test_el_ciclo_late_antes_de_cada_etapa_desde_su_propio_hilo(
    store, settings, env, context, plugins, fake, restic
):
    fake("ok")
    stages_done: list[int] = []
    threads = {threading.get_ident()}
    beaten_from: set[int] = set()

    def beat() -> None:
        beaten_from.add(threading.get_ident())
        stages_done.append(len(store.list(CYCLES_PREFIX)))

    outcome = cycle(store, settings, env, context, plugins, BeatingClock(TickClock(), beat))

    assert outcome.completed
    assert beaten_from == threads
    assert stages_done == sorted(stages_done)
    assert set(range(len(STAGES))) <= set(stages_done)
