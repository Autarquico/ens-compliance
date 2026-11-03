"""El latido del planificador: lo que lee el healthcheck del contenedor."""

import threading
from datetime import UTC, datetime

from ens_evidence.reports.store import CYCLES_PREFIX
from ens_evidence.scheduling.config import ScheduleConfig
from ens_evidence.scheduling.cycle import _BeatingClock
from ens_evidence.scheduling.scheduler import HEARTBEAT_FILE, Scheduler, file_heartbeat
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


def test_sin_latido_configurado_el_planificador_funciona():
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


def test_el_latido_escribe_en_el_directorio_de_estado(tmp_path):
    file_heartbeat({"ENS_STATE_DIR": str(tmp_path / "state")})()

    heartbeat = tmp_path / "state" / HEARTBEAT_FILE
    assert heartbeat.read_text().strip().isdigit()
    assert oct(heartbeat.parent.stat().st_mode & 0o777) == "0o700"


def test_si_no_puede_escribir_el_latido_no_rompe_el_planificador(tmp_path):
    blocker = tmp_path / "ocupado"
    blocker.write_text("no es un directorio")

    file_heartbeat({"ENS_STATE_DIR": str(blocker / "state")})()

    assert not (blocker / "state").exists()


class CountingClock(FakeClock):
    """Anota el orden de latidos y esperas para comprobar que el ciclo late entre medias."""

    def __init__(self, start: datetime, events: list[str]) -> None:
        super().__init__(start)
        self.events = events

    def sleep(self, seconds: float) -> None:
        self.events.append(f"sleep:{seconds:g}")
        super().sleep(seconds)


def test_el_reloj_del_ciclo_late_en_cada_lectura_y_parte_las_esperas_largas():
    events: list[str] = []
    clock = _BeatingClock(CountingClock(START, events), lambda: events.append("beat"))

    clock.now()
    clock.sleep(150)

    assert events == ["beat", "beat", "sleep:60", "beat", "sleep:60", "beat", "sleep:30"]


def test_una_espera_nula_late_una_vez():
    events: list[str] = []
    clock = _BeatingClock(CountingClock(START, events), lambda: events.append("beat"))

    clock.sleep(0)

    assert events == ["beat", "sleep:0"]


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

    outcome = cycle(store, settings, env, context, plugins, TickClock(), heartbeat=beat)

    assert outcome.completed
    assert beaten_from == threads
    assert stages_done == sorted(stages_done)
    assert set(range(len(STAGES))) <= set(stages_done)
