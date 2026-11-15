"""`MonitorLoop`: hilo con su propio intervalo; un tick fallido se registra y el bucle sigue."""

import logging
import threading

from ens_evidence.monitoring.monitor import Monitor

FIRST_TICK_DELAY_SECONDS = 30
_LOGGER = logging.getLogger("ens_evidence.monitoring")


class MonitorLoop(threading.Thread):
    """Primer tick tras un breve retardo (que arranque la caja) y luego cada `interval`."""

    def __init__(
        self,
        monitor: Monitor,
        stop: threading.Event,
        *,
        first_delay_seconds: float = FIRST_TICK_DELAY_SECONDS,
    ) -> None:
        super().__init__(name="monitor-loop", daemon=True)
        self._monitor = monitor
        self._stop_requested = stop
        self._first_delay = min(first_delay_seconds, monitor.interval_seconds)

    def run(self) -> None:
        delay = self._first_delay
        while not self._stop_requested.wait(delay):
            self.tick_safely()
            delay = self._monitor.interval_seconds

    def tick_safely(self) -> bool:
        try:
            self._monitor.tick()
        except Exception as error:  # noqa: BLE001 - nunca tumbar el scheduler por la monitorización
            _LOGGER.warning("tick de monitorización fallido (%s)", type(error).__name__)
            return False
        return True
