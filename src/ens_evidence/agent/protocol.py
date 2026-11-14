"""Lógica del protocolo remoto de osquery, sin HTTP.

Cualquier fallo de credencial lanza `NodeInvalid` antes de escribir nada (estado, workspace o
contacto); las rutas lo convierten siempre en la misma respuesta.
"""

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from ens_evidence.agent.node_key import (
    issue_node_key,
    normalize_host_identifier,
    verify_enroll_token,
    verify_node_key,
)
from ens_evidence.agent.osquery_config import SCHEDULE_INTERVAL_SECONDS, build_config, query_sql
from ens_evidence.agent.registry import RegistryError, enrolled_asset, load_registry
from ens_evidence.agent.results import (
    live_result,
    scheduled_result,
    snapshots_from_events,
    write_result,
)
from ens_evidence.agent.secrets import AgentSecrets
from ens_evidence.agent.state import AgentState, ResultBound
from ens_evidence.connectors.config import load_connector_config
from ens_evidence.endpoint.config import CONFIG_NAME, EndpointConfig
from ens_evidence.endpoint.package import CLOCK_SKEW
from ens_evidence.workspace import WorkspaceError, WorkspaceStore
from ens_evidence.workspace.cycle_lock import CycleBusy, CycleLock

MAX_HOSTNAME_CHARS = 255
KNOWN_PLATFORMS = frozenset({"darwin", "linux", "windows"})
# osquery desplaza cada intervalo hasta un 10 % (`--schedule_splay_percent`, 10 por defecto).
SCHEDULE_SPLAY = 0.10
# Un snapshot por consulta del paquete y equipo por intervalo de schedule, contado por su hora
# de generación; tres entregas por intervalo de recepción dejan pasar las atrasadas tras un corte.
RESULT_BOUND = ResultBound(
    spacing=timedelta(seconds=SCHEDULE_INTERVAL_SECONDS * (1 - SCHEDULE_SPLAY)) - CLOCK_SKEW,
    window=timedelta(seconds=SCHEDULE_INTERVAL_SECONDS),
    max_receipts=3,
)


class NodeInvalid(Exception):
    """Credencial o identidad no válida; no se distingue el motivo."""


class Unavailable(Exception):
    """Configuración, registro o almacenamiento ilegibles: el host debe reintentar."""


class MalformedBody(Exception):
    """Credencial válida, pero el cuerpo no tiene la forma que exige el endpoint."""

    def __init__(self, node: "Node") -> None:
        super().__init__("invalid body")
        self.node = node


class Busy(Exception):
    """El workspace tiene el lock tomado (ciclo o copia): el host debe reintentar."""


@dataclass(frozen=True)
class Node:
    asset_id: str
    host_identifier: str
    platform: str | None


@dataclass(frozen=True)
class Reply:
    """Respuesta al equipo y, si se autenticó, quién es (para el log, no para la respuesta)."""

    payload: dict[str, Any]
    node: Node
    outcome: str = "ok"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class AgentService:
    def __init__(
        self,
        store: WorkspaceStore,
        secrets: AgentSecrets,
        state: AgentState,
        new_lock: Callable[[], CycleLock],
        now: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._store = store
        self._secrets = secrets
        self._state = state
        self._new_lock = new_lock
        self._now = now
        self._writers = threading.Lock()

    def enroll(self, body: Any) -> Reply:
        fields = _mapping(body)
        host_identifier = _identifier(fields.get("host_identifier"))
        details = _system_info(fields)
        if host_identifier is None or details is None:
            raise NodeInvalid
        uuid, hostname = details
        if uuid != host_identifier:
            raise NodeInvalid
        asset_id = enrolled_asset(self._endpoint_config(), uuid, hostname)
        record = self._registry().agents.get(asset_id) if asset_id else None
        token = fields.get("enroll_secret")
        now = self._now()
        if record is None or record.revoked or not isinstance(token, str):
            raise NodeInvalid
        if not verify_enroll_token(self._secrets, token, uuid, record.generation, now):
            raise NodeInvalid
        assert asset_id is not None
        node = Node(asset_id, uuid, _build_platform(fields))
        self._state.touch_host(asset_id, now=now, platform=node.platform, force=True)
        node_key = issue_node_key(self._secrets, uuid, record.generation)
        return Reply({"node_key": node_key, "node_invalid": False}, node)

    def config(self, body: Any) -> Reply:
        node = self._authenticate(body)
        self._touch(node)
        return Reply({**build_config(), "node_invalid": False}, node)

    def log(self, body: Any) -> Reply:
        node = self._authenticate(body)
        fields = _mapping(body)
        events = fields.get("data")
        if fields.get("log_type") == "result":
            if not isinstance(events, list):
                raise MalformedBody(node)
            stored = self._store_snapshots(node, events)
        else:
            stored = True
        self._touch(node)
        return Reply({"node_invalid": False}, node, "ok" if stored else "throttled")

    def distributed_read(self, body: Any) -> Reply:
        node = self._authenticate(body)
        now = self._now()
        runs = self._state.deliver_pending(node.asset_id, now)
        self._touch(node)
        queries = {run.run_id: query_sql(run.query_id) for run in runs}
        return Reply({"queries": queries, "node_invalid": False}, node)

    def distributed_write(self, body: Any) -> Reply:
        node = self._authenticate(body)
        fields = _mapping(body)
        answers = fields.get("queries")
        if not isinstance(answers, dict):
            raise MalformedBody(node)
        statuses = fields.get("statuses")
        statuses = statuses if isinstance(statuses, dict) else {}
        if any(self._state.delivered_unanswered(node.asset_id, run_id) for run_id in answers):
            with self._workspace_lock():
                for run_id, rows in answers.items():
                    self._store_answer(node, run_id, rows, statuses.get(run_id, 0))
        self._touch(node)
        return Reply({"node_invalid": False}, node)

    def _store_snapshots(self, node: Node, events: list[Any]) -> bool:
        """`False` si algún snapshot se descarta por la cota (`RESULT_BOUND`)."""
        now = self._now()
        snapshots = snapshots_from_events(
            events, host_identifier=node.host_identifier, platform=node.platform, now=now
        )
        if not snapshots:
            return True
        with self._workspace_lock():
            fresh = [
                s
                for s in snapshots
                if self._state.result_slot_free(
                    node.asset_id, s.query_id, s.collected_at, now, RESULT_BOUND
                )
            ]
            if fresh:
                result = scheduled_result(node.asset_id, node.host_identifier, fresh)
                write_result(self._store, result, now=now)
                for s in fresh:
                    self._state.record_result(
                        node.asset_id, s.query_id, s.collected_at, now, RESULT_BOUND
                    )
        return len(fresh) == len(snapshots)

    def _store_answer(self, node: Node, run_id: str, rows: Any, status: Any) -> None:
        delivered = self._state.delivered_unanswered(node.asset_id, run_id)
        if delivered is None:
            return
        run, delivered_at = delivered
        code = status if isinstance(status, int) and not isinstance(status, bool) else 1
        now = self._now()
        result = live_result(
            node.asset_id,
            node.host_identifier,
            run_id=run_id,
            query_id=run.query_id,
            collected_at=delivered_at,
            status=code,
            raw_rows=rows,
        )
        evidence_id = write_result(self._store, result, now=now)
        self._state.record_answer(
            node.asset_id, run_id, evidence_id=evidence_id, status=code, now=now
        )

    def _authenticate(self, body: Any) -> Node:
        node_key = _mapping(body).get("node_key")
        identity = verify_node_key(self._secrets, node_key) if isinstance(node_key, str) else None
        if identity is None:
            raise NodeInvalid
        asset_id = _asset_for_uuid(self._endpoint_config(), identity.host_identifier)
        if asset_id is None or not self._registry().accepts(asset_id, identity.generation):
            raise NodeInvalid
        contact = self._state.host(asset_id)
        return Node(asset_id, identity.host_identifier, contact.platform if contact else None)

    def _touch(self, node: Node) -> None:
        self._state.touch_host(node.asset_id, now=self._now())

    def _endpoint_config(self) -> EndpointConfig:
        try:
            return load_connector_config(self._store, CONFIG_NAME, EndpointConfig)
        except WorkspaceError:
            raise Unavailable from None

    def _registry(self):
        try:
            return load_registry(self._store)
        except RegistryError:
            raise Unavailable from None

    @contextmanager
    def _workspace_lock(self) -> Iterator[None]:
        """Un escritor a la vez en este proceso y try-lock contra ciclos y copias."""
        with self._writers:
            lock = self._new_lock()
            try:
                lock.acquire()
            except CycleBusy:
                raise Busy from None
            try:
                yield
            except WorkspaceError:
                raise Unavailable from None
            finally:
                lock.release()


def _mapping(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise NodeInvalid
    return body


def _identifier(raw: Any) -> str | None:
    return normalize_host_identifier(raw) if isinstance(raw, str) else None


def _system_info(fields: dict[str, Any]) -> tuple[str, str] | None:
    """(uuid, hostname) de `host_details.system_info`; dato no fiable, solo se compara."""
    details = fields.get("host_details")
    info = details.get("system_info") if isinstance(details, dict) else None
    if not isinstance(info, dict):
        return None
    uuid, hostname = _identifier(info.get("uuid")), info.get("hostname")
    if uuid is None or not isinstance(hostname, str) or len(hostname) > MAX_HOSTNAME_CHARS:
        return None
    return uuid, hostname


def _build_platform(fields: dict[str, Any]) -> str | None:
    info = fields["host_details"].get("osquery_info")
    platform = info.get("build_platform") if isinstance(info, dict) else None
    return platform if platform in KNOWN_PLATFORMS else None


def _asset_for_uuid(config: EndpointConfig, host_identifier: str) -> str | None:
    for asset_id, host in config.hosts.items():
        if host.hardware_uuid.casefold() == host_identifier:
            return asset_id
    return None
