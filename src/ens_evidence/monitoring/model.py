"""Modelo del panel: lecturas por señal, elementos con su historia y la instantánea que pinta la UI.

Es operación, no cumplimiento: nada de aquí construye `CheckResult` ni toca la matriz. Una señal
ausente, inválida o vencida es `unknown`; nunca verde.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

GROUP_TOOLS = "herramientas"
GROUP_VPS = "vps"
GROUP_BOX = "caja"
GROUPS = (GROUP_TOOLS, GROUP_VPS, GROUP_BOX)
RUNBOOK = "monitorizacion"
FUTURE_SKEW = timedelta(minutes=5)


class Status(StrEnum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"
    UNKNOWN = "unknown"


_SEVERITY = {Status.OK: 0, Status.UNKNOWN: 1, Status.WARN: 2, Status.FAIL: 3}


def worst(statuses: list[Status]) -> Status:
    return max(statuses, key=_SEVERITY.__getitem__, default=Status.UNKNOWN)


@dataclass(frozen=True)
class Reading:
    """Lo que una sonda dice de un elemento en este tick."""

    id: str
    group: str
    title: str
    status: Status
    detail: str
    runbook: str = RUNBOOK
    value: float | None = None
    observed_at: datetime | None = None


def unknown(id: str, group: str, title: str, detail: str, runbook: str = RUNBOOK) -> Reading:
    return Reading(id, group, title, Status.UNKNOWN, detail, runbook)


def is_fresh(observed_at: datetime | None, ttl: timedelta, now: datetime) -> bool:
    """Vigente: con hora, no futura (más allá del desfase tolerado) y dentro de su caducidad."""
    return observed_at is not None and observed_at - FUTURE_SKEW <= now <= observed_at + ttl


@dataclass(frozen=True)
class Point:
    at: datetime
    status: Status
    value: float | None


@dataclass(frozen=True)
class Item:
    id: str
    group: str
    title: str
    status: Status
    detail: str
    runbook: str
    since: datetime | None
    observed_at: datetime | None
    value: float | None
    series: tuple[Point, ...]


@dataclass(frozen=True)
class MailState:
    configured: bool
    last_attempt: datetime | None = None
    last_ok: bool | None = None
    last_error: str | None = None


@dataclass(frozen=True)
class Snapshot:
    """Lo que renderiza `/monitorizacion`. `stale`: el bucle no ha corrido en 2 intervalos."""

    generated_at: datetime
    interval_seconds: int
    items: tuple[Item, ...]
    mail: MailState
    stale: bool = False

    def group(self, name: str) -> tuple[Item, ...]:
        return tuple(item for item in self.items if item.group == name)

    def failing(self) -> tuple[Item, ...]:
        return tuple(item for item in self.items if item.status is Status.FAIL)


def instant(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def parse_instant(raw: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else None
