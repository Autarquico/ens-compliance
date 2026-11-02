"""El reloj del planificador: la hora y una espera que `stop` puede interrumpir."""

import threading
from datetime import UTC, datetime


class SystemClock:
    def __init__(self, stop: threading.Event) -> None:
        self._stop = stop

    def now(self) -> datetime:
        return datetime.now(UTC)

    def sleep(self, seconds: float) -> None:
        self._stop.wait(seconds)
