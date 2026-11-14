"""Un tick de monitorización: sondas -> histórico -> avisos -> instantánea."""

import logging
import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path

import httpx

from ens_evidence.agent.monitoring import INTERVAL_SECONDS, MonitoringStore, load_monitoring_config
from ens_evidence.connectors.config import ConnectorConfigError, load_connector_config
from ens_evidence.connectors.keystore.config import KeystoreConfig
from ens_evidence.monitoring import box, tools, vps
from ens_evidence.monitoring.advisories import AdvisoryFeed
from ens_evidence.monitoring.alerts import AlertBook
from ens_evidence.monitoring.history import History
from ens_evidence.monitoring.mail import SmtpFactory, SmtpSettings, send_alert
from ens_evidence.monitoring.model import Item, Reading, Snapshot
from ens_evidence.monitoring.snapshot import write_heartbeat, write_snapshot
from ens_evidence.monitoring.thresholds import load_thresholds
from ens_evidence.workspace import WorkspaceStore
from ens_evidence.workspace.cycle_lock import resolve_state_dir

PROJECT_ENV = "ENS_BOX_PROJECT"
DEFAULT_PROJECT = "ens-box"
_LOGGER = logging.getLogger("ens_evidence.monitoring")


def _now() -> datetime:
    return datetime.now(UTC)


class Monitor:
    def __init__(
        self,
        store: WorkspaceStore,
        *,
        state_dir: Path | None = None,
        env: Mapping[str, str] | None = None,
        clock: Callable[[], datetime] = _now,
        github: httpx.Client | None = None,
        openbao_transport: httpx.BaseTransport | None = None,
        smtp_factory: SmtpFactory | None = None,
        interval_seconds: int = INTERVAL_SECONDS,
    ) -> None:
        self._store = store
        self._env = os.environ if env is None else env
        self._state_dir = state_dir or resolve_state_dir(self._env)
        self._clock = clock
        self._feed = AdvisoryFeed(self._state_dir, github)
        self._openbao_transport = openbao_transport
        self._smtp_factory = smtp_factory
        self.interval_seconds = interval_seconds

    def tick(self) -> Snapshot:
        now = self._clock()
        write_heartbeat(self._state_dir, now, self.interval_seconds)
        readings = self._readings(now)
        history = History(self._state_dir)
        history.append(readings, now)
        book = AlertBook(self._state_dir)
        due = book.update(readings, now)
        smtp = SmtpSettings.from_env(self._env)
        if due and smtp is not None:
            error = send_alert(
                smtp, [(r, book.since(r.id) or now) for r in due], factory=self._smtp_factory
            )
            book.record_mail(now, error is None, error)
            if error is None:
                book.mark_notified([r.id for r in due], now)
            else:
                _LOGGER.warning("no se pudo enviar el aviso por correo (%s)", error)
        book.save()
        series = history.series(now)
        items = tuple(
            Item(
                r.id,
                r.group,
                r.title,
                r.status,
                r.detail,
                r.runbook,
                book.since(r.id),
                r.observed_at,
                r.value,
                series.get(r.id, ()),
            )
            for r in readings
        )
        snapshot = Snapshot(now, self.interval_seconds, items, book.mail_state(smtp is not None))
        write_snapshot(self._state_dir, snapshot)
        write_heartbeat(self._state_dir, self._clock(), self.interval_seconds)
        return snapshot

    def _readings(self, now: datetime) -> list[Reading]:
        samples = MonitoringStore(self._state_dir).latest()
        limits = load_thresholds(self._store)
        config = load_monitoring_config(self._store)
        project = self._env.get(PROJECT_ENV) or DEFAULT_PROJECT
        seal = self._guarded(
            lambda: tools.probe_openbao(self._openbao_address(), self._openbao_transport), None
        )
        found: list[Reading] = []
        found += self._guarded(
            lambda: tools.tool_readings(self._store, samples, self._feed, project, seal, now), []
        )
        found += self._guarded(
            lambda: vps.vps_readings(
                samples, config.tls_hostnames if config else [], project, limits, now
            ),
            [],
        )
        found += self._guarded(lambda: _box(self._store, limits, now), [])
        return found

    def _openbao_address(self) -> str:
        try:
            return load_connector_config(self._store, "keystore", KeystoreConfig).address
        except ConnectorConfigError:
            return tools.DEFAULT_OPENBAO

    @staticmethod
    def _guarded[T](probe: Callable[[], T], fallback: T) -> T:
        """Una sonda que revienta no tumba el tick: se registra el tipo y se sigue."""
        try:
            return probe()
        except Exception as error:  # noqa: BLE001 - el bucle no debe morir por una sonda
            _LOGGER.warning("sonda fallida (%s)", type(error).__name__)
            return fallback


def _box(store: WorkspaceStore, limits, now: datetime) -> list[Reading]:
    optional = (box.silent_agents(store, limits, now), box.offsite_backup(store, limits, now))
    return [box.scheduler(store, now), *(r for r in optional if r is not None)]
