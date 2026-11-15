"""Histórico de 30 días: un JSONL por día en `state/monitoring/history/`, un único escritor."""

import json
import os
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from ens_evidence.monitoring.files import monitoring_dir, private_dir
from ens_evidence.monitoring.model import Point, Reading, Status, instant, parse_instant, worst

RETENTION_DAYS = 30
BUCKET = timedelta(hours=6)


def _ends_cleanly(fd: int) -> bool:
    size = os.fstat(fd).st_size
    return size == 0 or os.pread(fd, 1, size - 1) == b"\n"


class History:
    def __init__(self, state_dir: Path) -> None:
        self._dir = monitoring_dir(state_dir) / "history"

    def append(self, readings: list[Reading], now: datetime) -> None:
        """Añade una línea con una sola escritura `O_APPEND` y fsync; después poda."""
        line = json.dumps(
            {"t": instant(now), "items": {r.id: [r.status.value, r.value] for r in readings}},
            separators=(",", ":"),
        )
        private_dir(self._dir)
        path = self._dir / f"{now.astimezone(UTC).date().isoformat()}.jsonl"
        fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            lead = "" if _ends_cleanly(fd) else "\n"  # no pegar a un corte a medias
            os.write(fd, (lead + line + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        self._prune(now)

    def _prune(self, now: datetime) -> None:
        oldest = now.astimezone(UTC).date() - timedelta(days=RETENTION_DAYS)
        for path in self._dir.glob("*.jsonl"):
            try:
                day = date.fromisoformat(path.stem)
            except ValueError:
                continue
            if day < oldest:
                path.unlink(missing_ok=True)

    def series(self, now: datetime) -> dict[str, tuple[Point, ...]]:
        """Por elemento, un punto cada 6 h de los últimos 30 días: peor estado y último valor."""
        since = now - timedelta(days=RETENTION_DAYS)
        buckets: dict[str, dict[int, list[tuple[datetime, Status, float | None]]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for path in sorted(self._dir.glob("*.jsonl")):
            for at, items in self._lines(path):
                if at < since or at > now + timedelta(minutes=5):
                    continue
                slot = int(at.timestamp() // BUCKET.total_seconds())
                for item_id, (status, value) in items.items():
                    buckets[item_id][slot].append((at, status, value))
        return {
            item_id: tuple(self._point(slot, samples) for slot, samples in sorted(slots.items()))
            for item_id, slots in buckets.items()
        }

    @staticmethod
    def _point(slot: int, samples: list[tuple[datetime, Status, float | None]]) -> Point:
        last = max(samples, key=lambda s: s[0])
        return Point(
            datetime.fromtimestamp(slot * BUCKET.total_seconds(), UTC),
            worst([s for _, s, _ in samples]),
            last[2],
        )

    @staticmethod
    def _lines(path: Path):
        try:
            text = path.read_text("utf-8")
        except OSError:
            return
        for raw in text.splitlines():
            try:
                row = json.loads(raw)
                at = parse_instant(row["t"])
                items = {k: (Status(v[0]), v[1]) for k, v in row["items"].items()}
            except (ValueError, KeyError, TypeError, IndexError):
                continue  # una línea rota (corte a medias) no invalida el resto
            if at is not None:
                yield at, items
