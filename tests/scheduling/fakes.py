"""Reloj y ejecutor falsos del planificador: el tiempo avanza solo cuando se duerme."""

import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from ens_evidence.scheduling.calendar import Job
from ens_evidence.scheduling.config import ScheduleConfig
from ens_evidence.scheduling.cycle import Trigger
from ens_evidence.scheduling.scheduler import JobResult


class FakeClock:
    """`sleep` avanza el reloj sin esperar; `on_sleep` se llama tras cada espera.
    `auto_advance` mueve el reloj en cada lectura (los sellos de los informes lo piden)."""

    def __init__(self, start: datetime, *, auto_advance: timedelta = timedelta(0)) -> None:
        assert start.tzinfo is not None
        self.current = start.astimezone(UTC)
        self.auto_advance = auto_advance
        self.sleeps: list[float] = []
        self.on_sleep: Callable[[], None] | None = None

    def now(self) -> datetime:
        instant = self.current
        self.current += self.auto_advance
        return instant

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.current += timedelta(seconds=seconds)
        if self.on_sleep is not None:
            self.on_sleep()

    def stop_at(self, end: datetime, stop: threading.Event) -> None:
        """Pide parar en la primera espera que llegue a `end`."""

        def check() -> None:
            if self.current >= end:
                stop.set()

        self.on_sleep = check


class FakeExecutor:
    """Registra cada ejecución y responde según el guion; sin guion, termina bien.
    Al completar anota el periodo en `done`, como haría el workspace."""

    def __init__(
        self,
        clock: FakeClock,
        done: set[tuple[str, str]],
        script: list[JobResult] | None = None,
        *,
        duration: timedelta = timedelta(0),
    ) -> None:
        self.clock = clock
        self.done = done
        self.script = list(script or [])
        self.duration = duration
        self.runs: list[tuple[Job, Trigger, datetime]] = []
        self.active = 0
        self.max_active = 0
        self.during: Callable[[], None] | None = None
        self.raises: Exception | None = None

    @property
    def periods(self) -> list[tuple[str, str]]:
        return [(job.kind, job.period) for job, _, _ in self.runs]

    def run(self, job: Job, cfg: ScheduleConfig, trigger: Trigger) -> JobResult:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            self.runs.append((job, trigger, self.clock.current))
            if self.during is not None:
                self.during()
            if self.raises is not None:
                raise self.raises
            self.clock.current += self.duration
            result = self.script.pop(0) if self.script else JobResult("completed")
            if result.status == "completed":
                self.done.add((job.kind, job.period))
            return result
        finally:
            self.active -= 1
