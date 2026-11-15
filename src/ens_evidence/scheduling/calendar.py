"""Calendario del planificador: funciones puras, sin reloj ni disco.

Todo instante es UTC con zona horaria. El `period` de un trabajo es la fecha local de su
`due_at` (diario, `YYYY-MM-DD`) o el mes local (mensual, `YYYY-MM`); los reintentos lo conservan.
"""

import datetime as dt
from collections.abc import Collection
from dataclasses import dataclass
from typing import Literal
from zoneinfo import ZoneInfo

from ens_evidence.scheduling.config import ScheduleConfig

JobKind = Literal["daily", "monthly"]
PREAUDIT: Literal["preaudit"] = "preaudit"
AnyJobKind = Literal["daily", "monthly", "preaudit"]
"""`preaudit` no es del calendario: es una petición de la UI (`preaudit/requests.py`) y su
`period` es la clave de la petición. Se comprueba en cada tick, sin hora de disparo."""
UTC = dt.UTC


@dataclass(frozen=True)
class Job:
    kind: AnyJobKind
    period: str
    due_at: dt.datetime


def resolve_local(day: dt.date, at: dt.time, zone: ZoneInfo) -> dt.datetime:
    """Instante UTC de `day at` en `zone`.

    Hora inexistente: se desplaza por el salto (02:30 -> 03:30). Hora ambigua: primera ocurrencia.
    Con `fold=0`, zoneinfo usa el desplazamiento previo a la transición, que es lo que produce
    las dos reglas.
    """
    naive = dt.datetime.combine(day, at)
    return naive.replace(tzinfo=zone, fold=0).astimezone(UTC)


def local_date(instant: dt.datetime, zone: ZoneInfo) -> dt.date:
    return instant.astimezone(zone).date()


def _daily_due(day: dt.date, cfg: ScheduleConfig) -> dt.datetime:
    return resolve_local(day, cfg.daily.time, cfg.zone)


def _monthly_due(year: int, month: int, cfg: ScheduleConfig) -> dt.datetime:
    # Tras el diario del mismo día: misma hora; el planificador ejecuta el diario primero.
    return resolve_local(dt.date(year, month, cfg.monthly.day), cfg.daily.time, cfg.zone)


def daily_period(due_at: dt.datetime, cfg: ScheduleConfig) -> str:
    return local_date(due_at, cfg.zone).isoformat()


def monthly_period(due_at: dt.datetime, cfg: ScheduleConfig) -> str:
    return local_date(due_at, cfg.zone).strftime("%Y-%m")


def _require_aware(instant: dt.datetime) -> dt.datetime:
    if instant.tzinfo is None:
        raise ValueError("se esperaba un instante con zona horaria")
    return instant.astimezone(UTC)


def next_daily(after: dt.datetime, cfg: ScheduleConfig) -> dt.datetime:
    """Primer disparo diario estrictamente posterior a `after`."""
    after = _require_aware(after)
    start = local_date(after, cfg.zone) - dt.timedelta(days=1)
    for offset in range(4):
        due = _daily_due(start + dt.timedelta(days=offset), cfg)
        if due > after:
            return due
    raise AssertionError("inalcanzable: hay un disparo diario en cualquier ventana de 4 días")


def next_monthly(after: dt.datetime, cfg: ScheduleConfig) -> dt.datetime:
    """Primer disparo mensual estrictamente posterior a `after`."""
    after = _require_aware(after)
    local = after.astimezone(cfg.zone)
    year, month = local.year, local.month
    for _ in range(3):
        due = _monthly_due(year, month, cfg)
        if due > after:
            return due
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    raise AssertionError("inalcanzable: hay un disparo mensual en cualquier ventana de 3 meses")


def due_jobs(
    now: dt.datetime,
    cfg: ScheduleConfig,
    done: Collection[tuple[AnyJobKind, str]],
    requests: Collection[str] = (),
) -> list[Job]:
    """Trabajos vencidos y sin terminar en `now`, el diario primero.

    Solo el periodo en curso: antes de la hora no hay nada; pasada la hora se ejecuta si no está
    en `done`; el mensual pendiente se recupera durante todo el mes. No se rellenan días pasados.
    Las peticiones de pre-auditoría (`requests`: sus claves) vencen en el acto, tras los dos.
    """
    now = _require_aware(now)
    local = now.astimezone(cfg.zone)
    jobs: list[Job] = []
    daily = _daily_due(local.date(), cfg)
    if daily <= now and ("daily", daily_period(daily, cfg)) not in done:
        jobs.append(Job("daily", daily_period(daily, cfg), daily))
    monthly = _monthly_due(local.year, local.month, cfg)
    if monthly <= now and ("monthly", monthly_period(monthly, cfg)) not in done:
        jobs.append(Job("monthly", monthly_period(monthly, cfg), monthly))
    jobs += [Job(PREAUDIT, key, now) for key in sorted(requests) if (PREAUDIT, key) not in done]
    return jobs
