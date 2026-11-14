"""Almacén de muestras de monitorización en `$ENS_STATE_DIR/monitoring/vps/`.

No es evidencia ni entra en la copia. Un único escritor (el servidor de agentes) y escritura
atómica: un fichero `<consulta>.json` con la última muestra. Los lectores (el scheduler y la UI)
solo leen. Una muestra solo se reemplaza por otra más reciente, así que las reentregas y los
lotes atrasados no tocan nada.
"""

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ens_evidence.agent.monitoring.queries import (
    MONITORING_QUERIES,
    PLATFORM,
    MonitoringQuery,
)
from ens_evidence.endpoint.package import CLOCK_SKEW
from ens_evidence.workspace.cycle_lock import resolve_state_dir

MONITORING_DIR = "monitoring"
VPS_DIR = "vps"
EARLIEST_EVENT = datetime(2000, 1, 1, tzinfo=UTC)
MAX_VALUE_CHARS = 512
MAX_EVENTS_PER_BATCH = 32
_OTHERS_CAN_WRITE = 0o022


@dataclass(frozen=True)
class Sample:
    query_id: str
    observed_at: datetime
    rows: tuple[dict[str, str], ...]


def valid_samples(
    events: list[Any], *, host_identifier: str, platform: str | None, now: datetime
) -> list[Sample]:
    """La muestra más reciente válida por consulta; lo demás se ignora sin error."""
    if platform != PLATFORM:
        return []
    latest: dict[str, Sample] = {}
    for event in events[:MAX_EVENTS_PER_BATCH]:
        sample = _sample(event, host_identifier, now)
        if sample and (
            sample.query_id not in latest
            or sample.observed_at > latest[sample.query_id].observed_at
        ):
            latest[sample.query_id] = sample
    return list(latest.values())


class MonitoringStore:
    def __init__(self, state_dir: Path | None = None) -> None:
        self._dir = (state_dir or resolve_state_dir()) / MONITORING_DIR / VPS_DIR

    def record(self, samples: list[Sample]) -> int:
        """Guarda las muestras más recientes que las almacenadas; devuelve cuántas escribió."""
        written = 0
        for sample in samples:
            current = self.sample(sample.query_id)
            if current is not None and current.observed_at >= sample.observed_at:
                continue
            self._write(sample)
            written += 1
        return written

    def sample(self, query_id: str) -> Sample | None:
        """Última muestra de la consulta, o `None` si falta o está ilegible."""
        if query_id not in MONITORING_QUERIES:
            return None
        try:
            document = json.loads((self._dir / f"{query_id}.json").read_text("utf-8"))
            rows = tuple({str(k): str(v) for k, v in row.items()} for row in document["rows"])
            observed_at = datetime.fromisoformat(document["observed_at"])
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return None
        if observed_at.tzinfo is None:
            return None
        return Sample(query_id, observed_at, rows)

    def latest(self) -> dict[str, Sample]:
        """Última muestra de cada consulta que tenga una legible, con su `observed_at`."""
        found = {query_id: self.sample(query_id) for query_id in MONITORING_QUERIES}
        return {query_id: s for query_id, s in found.items() if s is not None}

    def _write(self, sample: Sample) -> None:
        self._dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        _require_private(self._dir)
        text = json.dumps(
            {
                "observed_at": sample.observed_at.isoformat(),
                "rows": list(sample.rows),
            }
        )
        fd, tmp = tempfile.mkstemp(dir=self._dir, prefix=".tmp-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self._dir / f"{sample.query_id}.json")
        except BaseException:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise


def _require_private(path: Path) -> None:
    info = path.stat()
    if info.st_uid != os.geteuid() or info.st_mode & _OTHERS_CAN_WRITE:
        raise PermissionError("el directorio de estado debe ser del usuario y no escribible")


def _sample(event: Any, host_identifier: str, now: datetime) -> Sample | None:
    if not isinstance(event, dict):
        return None
    name = event.get("name")
    query = MONITORING_QUERIES.get(name) if isinstance(name, str) else None
    reported = event.get("hostIdentifier")
    if (
        query is None
        or not isinstance(reported, str)
        or reported.strip().lower() != host_identifier
    ):
        return None
    observed_at = _event_time(event.get("unixTime"))
    if observed_at is None or not EARLIEST_EVENT <= observed_at <= now + CLOCK_SKEW:
        return None
    rows = _clean_rows(event.get("snapshot"), query)
    return None if rows is None else Sample(query.id, observed_at, rows)


def _event_time(raw: Any) -> datetime | None:
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None
    try:
        return datetime.fromtimestamp(raw, UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _clean_rows(raw: Any, query: MonitoringQuery) -> tuple[dict[str, str], ...] | None:
    """Filas con solo las columnas de la lista blanca; cualquier forma o tamaño inválido anula."""
    if not isinstance(raw, list) or len(raw) > query.max_rows:
        return None
    rows: list[dict[str, str]] = []
    for row in raw:
        if not isinstance(row, dict) or not all(
            isinstance(k, str) and isinstance(v, str) and len(v) <= MAX_VALUE_CHARS
            for k, v in row.items()
        ):
            return None
        rows.append({c: row[c] for c in query.columns if c in row})
    return tuple(rows)
