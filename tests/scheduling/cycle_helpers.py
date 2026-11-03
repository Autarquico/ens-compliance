"""Lo que comparten los tests del ciclo: constantes, el reloj que avanza y `cycle()`."""

from datetime import UTC, datetime, timedelta

from ens_evidence.scheduling.config import ScheduleConfig
from ens_evidence.scheduling.cycle import run_cycle

DEFAULT_CONFIG = ScheduleConfig()
DUE_AT = datetime(2026, 11, 1, 5, 0, tzinfo=UTC)  # 06:00 en Madrid
PERIOD = "2026-11-01"
STARTS = DUE_AT + timedelta(minutes=2)
STAGES = ["started", "collected", "checked", "reported", "backed_up"]


class Crash(BaseException):
    """Una caída del proceso: no la captura ningún `except Exception`."""


class TickClock:
    """Cada lectura avanza un segundo (las claves de los informes llevan microsegundos)."""

    def __init__(self, start: datetime = STARTS) -> None:
        self.current = start
        self.issued: list[datetime] = []

    def now(self) -> datetime:
        instant = self.current
        self.issued.append(instant)
        self.current += timedelta(seconds=1)
        return instant

    def sleep(self, seconds: float) -> None:
        self.current += timedelta(seconds=seconds)


def cycle(
    store,
    settings,
    env,
    context,
    plugins,
    clock,
    *,
    cfg=DEFAULT_CONFIG,
    job="daily",
    period=PERIOD,
    **extra,
):
    return run_cycle(
        context,
        store,
        settings,
        env,
        job,
        period,
        DUE_AT,
        clock=clock,
        cfg=cfg,
        trigger="scheduled",
        plugins=plugins,
        **extra,
    )
