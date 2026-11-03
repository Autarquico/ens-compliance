"""Almacén de informes y marcadores de etapa (solo adición, ADR 0008).

Un par JSON+HTML comparte sello y está *publicado* si existen los dos. Un fichero ilegible
nunca se ignora: se devuelve en `problems`/`Unavailable` con la clave y el motivo.
"""

import hashlib
import json
import re
from calendar import monthrange
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal

from pydantic import ValidationError

from ens_evidence.matrix.render import report_key
from ens_evidence.reports.model import (
    RUN_ID_PATTERN,
    Coverage,
    CycleStage,
    DailyReport,
    InterruptedCycle,
    Unavailable,
    is_day,
    is_month,
    render_json,
)
from ens_evidence.workspace import WorkspaceError, WorkspaceStore

REPORTS_PREFIX = "evidence/reports"
CYCLES_PREFIX = f"{REPORTS_PREFIX}/cycles"
Kind = Literal["daily", "monthly"]
STAGES = (
    "started",
    "collected",
    "checked",
    "reported",
    "backed_up",
    "backup_failed",
    "backup_skipped",
)
TERMINAL_STAGES = frozenset({"backed_up", "backup_failed", "backup_skipped"})
_RUN_ID = re.compile(RUN_ID_PATTERN)


@dataclass(frozen=True)
class Pair:
    period: str
    stamp: str
    json_key: str
    html_key: str | None

    @property
    def published(self) -> bool:
        return self.html_key is not None


@dataclass(frozen=True)
class Canonical:
    """Informe diario legible y verificado, base de comparación del siguiente."""

    pair: Pair
    report: DailyReport
    json_sha256: str
    html_sha256: str
    others: tuple[str, ...]


@dataclass(frozen=True)
class PeriodScan:
    coverage: Coverage
    days: dict[str, Canonical]
    problems: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class StageReading:
    stages: list[CycleStage]
    problems: list[str]


@dataclass(frozen=True)
class CycleScan:
    """`runs`: los ciclos leídos. `deferred`: los de `defer_finished` (con marcador terminal en
    el listado), sin leer todavía; se leen con `read_deferred`."""

    runs: dict[str, StageReading]
    listing_sha256: str
    deferred: dict[str, list[str]] = field(default_factory=dict)

    def read_deferred(self, store: WorkspaceStore, run_id: str) -> StageReading:
        return _read_run(store, run_id, self.deferred[run_id])


def _period_prefix(kind: Kind, period: str) -> str:
    valid = is_day(period) if kind == "daily" else is_month(period)
    if kind not in ("daily", "monthly") or not valid:
        raise ValueError("periodo no válido")
    return f"{REPORTS_PREFIX}/{kind}/{period}"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_json_strict(data: bytes) -> Any:
    """JSON sin claves repetidas (la última taparía a la primera)."""

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        keys = [k for k, _ in pairs]
        if len(set(keys)) != len(keys):
            raise ValueError("clave duplicada")
        return dict(pairs)

    return json.loads(data.decode("utf-8"), object_pairs_hook=unique)


# --- pares -----------------------------------------------------------------------------------


def save_pair(
    store: WorkspaceStore,
    kind: Kind,
    period: str,
    json_bytes: bytes,
    html_bytes: bytes,
    *,
    at: datetime,
) -> Pair:
    """Escribe JSON y luego HTML con un sello nuevo; `AlreadyExists` si la clave existe."""
    prefix = _period_prefix(kind, period)
    json_key = report_key(prefix, at, "json")
    html_key = report_key(prefix, at, "html")
    store.create_bytes(json_key, json_bytes)
    store.create_bytes(html_key, html_bytes)
    return _pair(period, json_key, html_key)


def save_html(store: WorkspaceStore, json_key: str, html_bytes: bytes) -> str:
    """Completa un par al que le falta el HTML (reanudación); el JSON no se toca."""
    if not json_key.endswith(".json") or not store.exists(json_key):
        raise ValueError("no hay JSON al que añadir HTML")
    html_key = json_key.removesuffix(".json") + ".html"
    store.create_bytes(html_key, html_bytes)
    return html_key


def _pair(period: str, json_key: str, html_key: str | None) -> Pair:
    stamp = json_key.rsplit("/", 1)[1].removesuffix(".json")
    return Pair(period, stamp, json_key, html_key)


def pairs(store: WorkspaceStore, kind: Kind, period: str) -> list[Pair]:
    """Pares del periodo por sello. Un `.html` sin `.json` aparece con `json_key` vacío."""
    prefix = _period_prefix(kind, period)
    keys = set(store.list(prefix))
    names = (key.removeprefix(prefix + "/") for key in keys)
    stamps = sorted({n.rsplit(".", 1)[0] for n in names if n.endswith((".json", ".html"))})
    result = []
    for stamp in stamps:
        json_key, html_key = f"{prefix}/{stamp}.json", f"{prefix}/{stamp}.html"
        result.append(
            Pair(
                period,
                stamp,
                json_key if json_key in keys else "",
                html_key if html_key in keys else None,
            )
        )
    return result


def published(store: WorkspaceStore, kind: Kind, period: str) -> bool:
    return any(p.published and p.json_key for p in pairs(store, kind, period))


# --- lectura verificada -----------------------------------------------------------------------


def published_hashes(store: WorkspaceStore, json_key: str) -> tuple[str, str]:
    """Huellas (JSON, HTML) del par publicado; las anota la etapa `reported`."""
    html_key = json_key.removesuffix(".json") + ".html"
    return _sha(store.read_bytes(json_key)), _sha(store.read_bytes(html_key))


def published_problem(
    store: WorkspaceStore, run_id: str, json_key: str, json_bytes: bytes, html_bytes: bytes
) -> str | None:
    """Por qué el par no es el que publicó el ciclo `run_id`, o None si coincide con su marcador
    `reported`. Un informe editado después de publicarse nunca cuenta como bueno."""
    try:
        marker = CycleStage.model_validate(
            load_json_strict(store.read_bytes(_stage_key(run_id, "reported")))
        )
    except (WorkspaceError, ValueError, ValidationError):
        return "falta su marcador de ciclo `reported` o es ilegible"
    if marker.reference != json_key:
        return "su marcador de ciclo cita otro informe"
    if (marker.json_sha256, marker.html_sha256) != (_sha(json_bytes), _sha(html_bytes)):
        return "no coincide con la huella de su publicación: se editó después de publicarse"
    return None


def _read_daily(
    store: WorkspaceStore, pair: Pair
) -> tuple[DailyReport, bytes, bytes] | Unavailable:
    if not pair.json_key:
        return Unavailable(reason=f"{pair.period}/{pair.stamp}: falta el JSON del par")
    if pair.html_key is None:
        return Unavailable(reason=f"{pair.json_key}: par incompleto, falta el HTML")
    try:
        json_bytes = store.read_bytes(pair.json_key)
        html_bytes = store.read_bytes(pair.html_key)
        report = DailyReport.model_validate(load_json_strict(json_bytes))
    except (WorkspaceError, ValueError, ValidationError):
        return Unavailable(reason=f"{pair.json_key}: ilegible o no válido")
    if report.period != pair.period or not html_bytes.strip():
        return Unavailable(reason=f"{pair.json_key}: no coincide con su periodo o HTML vacío")
    return report, json_bytes, html_bytes


def _canonical_of_day(store: WorkspaceStore, day_pairs: list[Pair]) -> Canonical | Unavailable:
    """El último informe *de ciclo* por sello, verificado contra su marcador `reported`.

    Los informes sin ciclo (manuales) no cuentan: rellenarían días sin ciclo. Un último ilegible
    o editado no se sustituye en silencio por uno anterior; los demás se listan."""
    for pair in reversed(day_pairs):
        loaded = _read_daily(store, pair)
        if isinstance(loaded, Unavailable):
            return loaded
        report, json_bytes, html_bytes = loaded
        if report.cycle is None:
            continue
        problem = published_problem(
            store, report.cycle.run_id, pair.json_key, json_bytes, html_bytes
        )
        if problem is not None:
            return Unavailable(reason=f"{pair.json_key}: {problem}")
        others = tuple(p.json_key for p in day_pairs if p is not pair and p.json_key)
        return Canonical(pair, report, _sha(json_bytes), _sha(html_bytes), others)
    return Unavailable(reason=f"{day_pairs[0].period}: ningún informe de ciclo verificado")


def _daily_periods(store: WorkspaceStore) -> list[str]:
    prefix = f"{REPORTS_PREFIX}/daily/"
    found = {key[len(prefix) :].split("/", 1)[0] for key in store.list(prefix.rstrip("/"))}
    return sorted(p for p in found if is_day(p))


def canonical_previous_daily(store: WorkspaceStore, before_period: str) -> Canonical | Unavailable:
    """Base del «diario anterior»: el informe de la fecha anterior más reciente con ficheros.

    Sin informes previos o con el último ilegible/incompleto -> `Unavailable`: nunca «sin cambios»
    sin base."""
    if not is_day(before_period):
        raise ValueError("periodo no válido")
    previous = [p for p in _daily_periods(store) if p < before_period]
    if not previous:
        return Unavailable(reason="no hay informe diario anterior")
    day_pairs = pairs(store, "daily", previous[-1])
    return _canonical_of_day(store, day_pairs)


def reports_in_period(
    store: WorkspaceStore, month: str, *, through: date | None = None
) -> PeriodScan:
    """Informes diarios canónicos del mes (hasta `through`) y la cobertura resultante."""
    if not is_month(month):
        raise ValueError("periodo no válido")
    year, number = int(month[:4]), int(month[5:])
    last_day = monthrange(year, number)[1]
    if through is not None and (through.year, through.month) == (year, number):
        last_day = through.day
    days: dict[str, Canonical] = {}
    problems: list[str] = []
    for period in (
        p for p in _daily_periods(store) if p.startswith(month) and int(p[8:]) <= last_day
    ):
        found = _canonical_of_day(store, pairs(store, "daily", period))
        if isinstance(found, Unavailable):
            problems.append(found.reason)
        else:
            days[period] = found
    return PeriodScan(Coverage.of(len(days), last_day), days, problems)


# --- etapas ------------------------------------------------------------------------------------


def _require_run_id(run_id: str) -> None:
    if not _RUN_ID.match(run_id):
        raise ValueError("run_id no válido")


def _stage_key(run_id: str, stage: str) -> str:
    _require_run_id(run_id)
    return f"{CYCLES_PREFIX}/{run_id}.{stage}.json"


def write_stage(
    store: WorkspaceStore,
    run_id: str,
    stage: str,
    *,
    at: datetime,
    period: str,
    reference: str | None = None,
    **details: Any,
) -> str:
    """Marcador solo de adición: repetir la etapa del mismo `run_id` lanza `AlreadyExists`.

    `details`: los campos tipados de la etapa (`attempts`, `checks`, `backup_code`, huellas)."""
    marker = CycleStage(
        run_id=run_id, stage=stage, at=at, period=period, reference=reference, **details
    )
    key = _stage_key(run_id, stage)
    store.create_bytes(key, render_json(marker))
    return key


def run_ids(store: WorkspaceStore) -> list[str]:
    return sorted(_group_by_run(store.list(CYCLES_PREFIX)))


def _group_by_run(keys: Iterable[str]) -> dict[str, list[str]]:
    """Las claves de marcadores por `run_id`; sin ningún `.json` no hay ciclo."""
    grouped: dict[str, list[str]] = {}
    for key in keys:
        run_id = key.rsplit("/", 1)[1].split(".")[0]
        if _RUN_ID.match(run_id) and key.startswith(f"{CYCLES_PREFIX}/{run_id}."):
            grouped.setdefault(run_id, []).append(key)
    return {run: found for run, found in grouped.items() if any(k.endswith(".json") for k in found)}


def _stage_of(run_id: str, key: str) -> str:
    return key.removeprefix(f"{CYCLES_PREFIX}/{run_id}.").removesuffix(".json")


def _read_run(store: WorkspaceStore, run_id: str, keys: Iterable[str]) -> StageReading:
    stages: list[CycleStage] = []
    problems: list[str] = []
    prefix = f"{CYCLES_PREFIX}/{run_id}."
    for key in keys:
        name = key.removeprefix(prefix).removesuffix(".json")
        if not key.endswith(".json") or name not in STAGES:
            problems.append(f"{key}: marcador desconocido")
            continue
        try:
            marker = CycleStage.model_validate(load_json_strict(store.read_bytes(key)))
        except (WorkspaceError, ValueError, ValidationError):
            problems.append(f"{key}: marcador ilegible o no válido")
            continue
        if marker.run_id != run_id or marker.stage != name:
            problems.append(f"{key}: no coincide con su nombre")
            continue
        stages.append(marker)
    stages.sort(key=lambda m: STAGES.index(m.stage))
    return StageReading(stages, problems)


def stages_of(store: WorkspaceStore, run_id: str) -> StageReading:
    """Etapas registradas en orden del ciclo; marcadores ilegibles o incoherentes -> `problems`."""
    _require_run_id(run_id)
    prefix = f"{CYCLES_PREFIX}/{run_id}."
    return _read_run(store, run_id, (k for k in store.list(CYCLES_PREFIX) if k.startswith(prefix)))


def scan_cycles(
    store: WorkspaceStore, *, since: str | None = None, defer_finished: bool = False
) -> CycleScan:
    """Todos los ciclos con un único listado del almacén. `since`: sello `YYYYmmddTHHMMSS`
    mínimo del `run_id` (los anteriores no se leen). `defer_finished`: los ciclos con marcador
    terminal en el listado no se leen (más recientes primero en `deferred`); así el coste de leer
    no crece con la historia. La huella cita lo listado."""
    keys = sorted(store.list(CYCLES_PREFIX))
    runs: dict[str, StageReading] = {}
    deferred: dict[str, list[str]] = {}
    for run_id, found in sorted(_group_by_run(keys).items()):
        if since is not None and run_id[: len(since)] < since:
            continue
        if defer_finished and any(_stage_of(run_id, k) in TERMINAL_STAGES for k in found):
            deferred[run_id] = found
        else:
            runs[run_id] = _read_run(store, run_id, found)
    newest_first = dict(reversed(deferred.items()))
    return CycleScan(runs, _sha("\n".join(keys).encode("utf-8")), newest_first)


def first_stage(reading: StageReading, stage: str) -> CycleStage | None:
    return next((m for m in reading.stages if m.stage == stage), None)


def reading_finished(reading: StageReading) -> bool:
    """Un ciclo diario termina con su etapa final (hecha, fallida u omitida); el mensual, que
    solo informa, con `reported`. Con algún marcador ilegible nunca está terminado."""
    if reading.problems:
        return False
    started = first_stage(reading, "started")
    monthly = started is not None and is_month(started.period)
    final = TERMINAL_STAGES | {"reported"} if monthly else TERMINAL_STAGES
    return any(m.stage in final for m in reading.stages)


def cycle_finished(store: WorkspaceStore, run_id: str) -> bool:
    return reading_finished(stages_of(store, run_id))


def interrupted_in(
    runs: Mapping[str, StageReading], *, now: datetime | None
) -> tuple[list[InterruptedCycle], list[str]]:
    """Ciclos sin terminar y legibles, y los `run_id` que no se pueden leer: con algún marcador
    ilegible, sin `started` o (con `now`) con marcadores fechados en el futuro, que no pueden
    ser de un ciclo real."""
    interrupted: list[InterruptedCycle] = []
    unreadable: list[str] = []
    for run_id, reading in runs.items():
        if reading_finished(reading):
            continue
        started = first_stage(reading, "started")
        future = now is not None and any(m.at > now for m in reading.stages)
        if started is None or reading.problems or future:
            unreadable.append(run_id)
        else:
            interrupted.append(
                InterruptedCycle(
                    run_id=run_id, last_stage=reading.stages[-1].stage, started_at=started.at
                )
            )
    return interrupted, unreadable
