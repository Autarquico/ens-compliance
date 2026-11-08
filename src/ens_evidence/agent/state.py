"""Estado operativo del servicio bajo `$ENS_STATE_DIR/agent/`: último contacto y consultas en vivo.

No es evidencia ni entra en la copia: se puede perder sin perder cumplimiento. Directorios
privados, escrituras atómicas y marcas de entrega/respuesta de creación exclusiva.
"""

import json
import os
import re
import secrets
import tempfile
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from ens_evidence.agent.osquery_config import pack_queries
from ens_evidence.workspace.cycle_lock import resolve_state_dir

AGENT_DIR = "agent"
CONTACT_WRITE_INTERVAL = timedelta(seconds=60)
_OTHERS_CAN_WRITE = 0o022
_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_RUN_ID = re.compile(r"live-[0-9a-f]{16}")


def is_run_id(value: object) -> bool:
    return isinstance(value, str) and _RUN_ID.fullmatch(value) is not None


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class HostContact(_Model):
    asset_id: str
    last_contact: AwareDatetime
    platform: str | None = None


class Run(_Model):
    run_id: str
    query_id: str
    targets: tuple[str, ...] = Field(min_length=1)
    created_by: str
    created_at: AwareDatetime
    expires_at: AwareDatetime


class Answer(_Model):
    evidence_id: str | None
    status: int
    answered_at: AwareDatetime


class Delivery(_Model):
    delivered_at: AwareDatetime


@dataclass(frozen=True)
class HostRun:
    asset_id: str
    delivered_at: datetime | None
    answer: Answer | None


@dataclass(frozen=True)
class RunStatus:
    run: Run
    hosts: tuple[HostRun, ...]


class AgentState:
    def __init__(self, state_dir: Path | None = None) -> None:
        self._root = (state_dir or resolve_state_dir()) / AGENT_DIR
        self._written: dict[str, tuple[datetime, str | None]] = {}
        self._results: dict[tuple[str, str], datetime] = {}
        self._guard = threading.Lock()

    # --- último contacto -------------------------------------------------------------------

    def touch_host(
        self, asset_id: str, *, now: datetime, platform: str | None = None, force: bool = False
    ) -> None:
        """Anota el contacto; reescribe como mucho cada 60 s salvo cambio de plataforma."""
        _safe(asset_id)
        with self._guard:
            previous = self._written.get(asset_id) or self._cached(asset_id)
            keep = previous[1] if platform is None and previous else platform
            fresh = previous and now - previous[0] < CONTACT_WRITE_INTERVAL
            if fresh and not force and keep == previous[1]:
                return
            contact = HostContact(asset_id=asset_id, last_contact=now, platform=keep)
            self._replace(self._root / "hosts" / f"{asset_id}.json", contact.model_dump_json())
            self._written[asset_id] = (now, keep)

    def host(self, asset_id: str) -> HostContact | None:
        _safe(asset_id)
        return self._read(self._root / "hosts" / f"{asset_id}.json", HostContact)

    def list_hosts(self) -> list[HostContact]:
        folder = self._root / "hosts"
        contacts = (self._read(path, HostContact) for path in _files(folder, ".json"))
        return sorted((c for c in contacts if c), key=lambda contact: contact.asset_id)

    def _cached(self, asset_id: str) -> tuple[datetime, str | None] | None:
        stored = self.host(asset_id)
        return (stored.last_contact, stored.platform) if stored else None

    # --- cota de resultados programados ----------------------------------------------------

    def result_slot_free(
        self, asset_id: str, query_id: str, now: datetime, spacing: timedelta
    ) -> bool:
        """`True` si el activo no ha entregado un snapshot de esa consulta en el último `spacing`.

        Solo en memoria: reiniciar el servicio reabre la ventana, a lo sumo un snapshot por
        consulta y equipo.
        """
        with self._guard:
            last = self._results.get((asset_id, query_id))
        return last is None or now - last >= spacing

    def record_results(self, asset_id: str, query_ids: Iterable[str], now: datetime) -> None:
        with self._guard:
            for query_id in query_ids:
                self._results[(asset_id, query_id)] = now

    # --- consultas en vivo -----------------------------------------------------------------

    def enqueue(
        self,
        query_id: str,
        assets: Iterable[str],
        expires_at: datetime,
        by: str,
        *,
        now: datetime | None = None,
    ) -> Run:
        """Encola una consulta del paquete para esos activos; sin SQL libre."""
        targets = tuple(dict.fromkeys(assets))
        created = now or datetime.now(UTC)
        if query_id not in pack_queries():
            raise ValueError("la consulta no está en el paquete")
        if not targets:
            raise ValueError("falta al menos un activo")
        if expires_at.tzinfo is None or expires_at <= created:
            raise ValueError("la caducidad debe ser futura y llevar zona horaria")
        for asset_id in targets:
            _safe(asset_id)
        run = Run(
            run_id=f"live-{secrets.token_hex(8)}",
            query_id=query_id,
            targets=targets,
            created_by=by,
            created_at=created,
            expires_at=expires_at,
        )
        self._replace(self._queue(run.run_id) / "query.json", run.model_dump_json())
        return run

    def list_runs(self, *, active_at: datetime | None = None) -> list[Run]:
        """Todas las consultas, o solo las que siguen vigentes en `active_at`."""
        runs = (self._load_run(path.name) for path in _dirs(self._root / "queue"))
        live = (r for r in runs if r and (active_at is None or r.expires_at > active_at))
        return sorted(live, key=lambda run: (run.created_at, run.run_id))

    def show_run(self, run_id: str) -> RunStatus | None:
        run = self._load_run(run_id) if is_run_id(run_id) else None
        if run is None:
            return None
        hosts = tuple(
            HostRun(asset, self._delivery(run_id, asset), self._answer(run_id, asset))
            for asset in run.targets
        )
        return RunStatus(run, hosts)

    def deliver_pending(self, asset_id: str, now: datetime) -> list[Run]:
        """Runs sin entregar y sin caducar para el activo; cada uno se entrega una sola vez."""
        _safe(asset_id)
        delivered: list[Run] = []
        for run in self.list_runs(active_at=now):
            if asset_id not in run.targets:
                continue
            marker = Delivery(delivered_at=now).model_dump_json()
            if self._create(self._queue(run.run_id) / f"delivered-{asset_id}", marker):
                delivered.append(run)
        return delivered

    def delivered_unanswered(self, asset_id: str, run_id: str) -> tuple[Run, datetime] | None:
        """El run, y el instante de entrega, si se entregó a este activo y aún no respondió."""
        if not is_run_id(run_id):
            return None
        _safe(asset_id)
        run = self._load_run(run_id)
        delivery = self._delivery(run_id, asset_id) if run else None
        if run is None or delivery is None or self._answer(run_id, asset_id) is not None:
            return None
        return run, delivery

    def record_answer(
        self, asset_id: str, run_id: str, *, evidence_id: str | None, status: int, now: datetime
    ) -> bool:
        """Marca la respuesta de creación exclusiva; `False` si ya estaba respondida."""
        _safe(asset_id)
        answer = Answer(evidence_id=evidence_id, status=status, answered_at=now)
        return self._create(
            self._queue(run_id) / f"answered-{asset_id}.json", answer.model_dump_json()
        )

    # --- ficheros --------------------------------------------------------------------------

    def _queue(self, run_id: str) -> Path:
        return self._root / "queue" / run_id

    def _load_run(self, run_id: str) -> Run | None:
        return self._read(self._queue(run_id) / "query.json", Run)

    def _delivery(self, run_id: str, asset_id: str) -> datetime | None:
        marker = self._read(self._queue(run_id) / f"delivered-{asset_id}", Delivery)
        return marker.delivered_at if marker else None

    def _answer(self, run_id: str, asset_id: str) -> Answer | None:
        return self._read(self._queue(run_id) / f"answered-{asset_id}.json", Answer)

    @staticmethod
    def _read[M: BaseModel](path: Path, model: type[M]) -> M | None:
        try:
            return model.model_validate(json.loads(path.read_bytes()))
        except (OSError, ValueError, ValidationError):
            return None

    def _replace(self, path: Path, text: str) -> None:
        _private_directory(path.parent)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
            os.replace(tmp, path)
        except BaseException:
            _discard(tmp)
            raise

    def _create(self, path: Path, text: str) -> bool:
        """Creación exclusiva y atómica (el contenido aparece completo o no aparece)."""
        _private_directory(path.parent)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
            os.link(tmp, path)
            return True
        except FileExistsError:
            return False
        finally:
            _discard(tmp)


def _safe(name: str) -> str:
    if not _SAFE_NAME.fullmatch(name):
        raise ValueError("identificador de activo no apto para el estado")
    return name


def _private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.stat()
    if info.st_uid != os.geteuid() or info.st_mode & _OTHERS_CAN_WRITE:
        raise PermissionError("el directorio de estado debe ser del usuario y no escribible")


def _discard(name: str) -> None:
    try:
        os.unlink(name)
    except FileNotFoundError:
        pass


def _files(folder: Path, suffix: str) -> list[Path]:
    try:
        return sorted(p for p in folder.iterdir() if p.suffix == suffix and p.is_file())
    except OSError:
        return []


def _dirs(folder: Path) -> list[Path]:
    try:
        return sorted(p for p in folder.iterdir() if p.is_dir() and is_run_id(p.name))
    except OSError:
        return []
