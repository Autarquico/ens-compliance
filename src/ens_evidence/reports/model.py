"""Contrato de los informes diario y mensual (guía :905-941).

Cada dato lleva `sources` (`Sourced`) o es `Unavailable` con su motivo: lo que no se pudo
obtener nunca se presenta como 0 ni como «sin cambios». Ningún campo expresa «cumple» (regla 12).
"""

import json
import re
from datetime import date, timedelta
from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from ens_evidence.assessment.render import NO_CONFORMITY
from ens_evidence.incidents.model import INCIDENT_ID_PATTERN, RecordKind
from ens_evidence.models.contracts import SHA256_PATTERN, Strict
from ens_evidence.models.enums import CheckStatus, CollectionStatus, Readiness
from ens_evidence.orgdocs.approval import DocumentState

DAY_PATTERN = r"^\d{4}-\d{2}-\d{2}$"
MONTH_PATTERN = r"^\d{4}-\d{2}$"
RUN_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
_DAY = re.compile(DAY_PATTERN)
_MONTH = re.compile(MONTH_PATTERN)

SourceKind = Literal[
    "evidence",
    "check_run",
    "collection",
    "review",
    "config",
    "inventory",
    "document",
    "approval",
    "scope",
    "risk",
    "backup_run",
    "restore_test",
    "report",
    "incident",
    "cycle",
]


class SourceRef(Strict):
    kind: SourceKind
    key: str = Field(min_length=1)
    sha256: str = Field(pattern=SHA256_PATTERN)
    at: AwareDatetime


class Unavailable(Strict):
    reason: str = Field(min_length=1)


class Sourced[T](Strict):
    value: T
    sources: list[SourceRef] = Field(min_length=1)


type Section[T] = Sourced[T] | Unavailable


class ExpiryItem(Strict):
    kind: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    expires_at: AwareDatetime
    days_left: int = Field(description="Negativo: ya vencido.")
    sources: list[SourceRef] = Field(min_length=1)


class Coverage(Strict):
    days_with_report: int = Field(ge=0)
    days_in_period: int = Field(ge=0)
    partial: bool

    @model_validator(mode="after")
    def _consistent(self) -> "Coverage":
        if self.days_with_report > self.days_in_period:
            raise ValueError("days_with_report supera days_in_period")
        if self.partial != (self.days_with_report < self.days_in_period):
            raise ValueError("partial no coincide con la cobertura")
        return self

    @classmethod
    def of(cls, days_with_report: int, days_in_period: int) -> "Coverage":
        return cls(
            days_with_report=days_with_report,
            days_in_period=days_in_period,
            partial=days_with_report < days_in_period,
        )


class ConnectorAttempts(Strict):
    connector: str = Field(min_length=1)
    attempts: int = Field(ge=0)
    last_status: CollectionStatus | None = None


class CycleInfo(Strict):
    run_id: str = Field(pattern=RUN_ID_PATTERN)
    trigger: Literal["scheduled", "manual", "recovery"]
    period: str
    due_at: AwareDatetime
    started_at: AwareDatetime
    data_cutoff: AwareDatetime
    connector_attempts: list[ConnectorAttempts] = Field(default_factory=list)


class CheckCounts(Strict):
    counts: dict[CheckStatus, int]


class ConnectorEntry(Strict):
    connector: str = Field(min_length=1)
    collection: Section[CollectionStatus]
    checks: Section[CheckCounts]
    attempts: int | None = Field(default=None, ge=0)


class AssetsSummary(Strict):
    total: int = Field(ge=0)
    by_type: dict[str, int]


class DocumentEntry(Strict):
    """Estado de un documento organizativo. Sin caducidad (ADR 0010): solo el estado."""

    name: str = Field(min_length=1)
    state: DocumentState
    approved_at: AwareDatetime | None = None
    reasons: list[str] = Field(default_factory=list)


class ReadinessValue(Strict):
    readiness: Readiness
    blockers: list[str]


class ChangeItem(Strict):
    subject: str = Field(min_length=1)
    change: Literal["added", "removed", "changed"]


class Changes(Strict):
    base_period: str = Field(pattern=DAY_PATTERN)
    items: list[ChangeItem]


class InterruptedCycle(Strict):
    run_id: str = Field(pattern=RUN_ID_PATTERN)
    last_stage: str
    started_at: AwareDatetime


class IncidentRecordRef(Strict):
    seq: int = Field(ge=1)
    kind: RecordKind
    recorded_at: AwareDatetime


class IncidentEntry(Strict):
    """Un incidente del registro. Con `problems` está roto: se muestra su error y ningún valor
    (no se puede fiar de lo que declara); sin ellos, todos los valores existen."""

    id: str = Field(pattern=INCIDENT_ID_PATTERN)
    title: str | None = None
    severity: str | None = Field(default=None, description="Declarada; None si no se declaró.")
    detected_at: AwareDatetime | None = None
    simulated: bool | None = None
    last_record: IncidentRecordRef | None = None
    problems: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _broken_or_complete(self) -> "IncidentEntry":
        values = (self.title, self.detected_at, self.simulated, self.last_record)
        if self.problems and (self.severity is not None or any(v is not None for v in values)):
            raise ValueError("un incidente roto no lleva valores")
        if not self.problems and any(v is None for v in values):
            raise ValueError("un incidente verificado lleva todos sus valores")
        return self


class Entry(Strict):
    subject: str = Field(min_length=1)
    detail: str


class Window(Strict):
    """Mes natural local, guardado en UTC: `start` incluido, `end` excluido."""

    start: AwareDatetime
    end: AwareDatetime

    @model_validator(mode="after")
    def _utc_and_ordered(self) -> "Window":
        if self.start.utcoffset() != timedelta(0) or self.end.utcoffset() != timedelta(0):
            raise ValueError("la ventana se guarda en UTC")
        if self.start >= self.end:
            raise ValueError("la ventana termina antes de empezar")
        return self


class Aggregate(Strict):
    label: str | None = Field(default=None, description="Rótulo de la sección si no es obvio.")
    coverage: Coverage
    counts: dict[str, int] = Field(default_factory=dict)
    items: list[Entry] = Field(default_factory=list)


class _Report(Strict):
    schema_version: str = "1.0"
    generated_at: AwareDatetime
    product_version: str
    notices: list[str]

    @model_validator(mode="after")
    def _carries_disclaimer(self) -> "_Report":
        if NO_CONFORMITY not in self.notices:
            raise ValueError("falta el aviso de no conformidad")
        return self


class DailyReport(_Report):
    kind: Literal["daily"] = "daily"
    period: str = Field(pattern=DAY_PATTERN)
    cycle: CycleInfo | None
    assets: Section[AssetsSummary]
    assets_without_agent: Section[list[str]]
    checks: Section[CheckCounts]
    connectors: list[ConnectorEntry]
    readiness: Section[ReadinessValue]
    documents: Section[list[DocumentEntry]]
    expiring: Section[list[ExpiryItem]]
    critical_alerts: Section[list[str]]
    open_incidents: Section[list[IncidentEntry]]
    changes: Section[Changes]
    interrupted_cycles: Section[list[InterruptedCycle]]

    @model_validator(mode="after")
    def _real_date(self) -> "DailyReport":
        date.fromisoformat(self.period)
        return self


class MonthlyReport(_Report):
    kind: Literal["monthly"] = "monthly"
    period: str = Field(pattern=MONTH_PATTERN)
    window: Window
    cycle: CycleInfo | None
    inventory: Section[Aggregate]
    changes: Section[Aggregate]
    deviations: Section[Aggregate]
    evidence: Section[Aggregate]
    incidents: Section[Aggregate]
    vulnerabilities: Section[Aggregate]
    backups: Section[Aggregate]
    restore_tests: Section[Aggregate]
    access_reviews: Section[Aggregate]
    pending: Section[Aggregate]
    corrective_actions: Section[Aggregate]
    daily_reports: Section[Aggregate]

    @model_validator(mode="after")
    def _real_month(self) -> "MonthlyReport":
        date.fromisoformat(f"{self.period}-01")
        return self


class CycleStage(Strict):
    run_id: str = Field(pattern=RUN_ID_PATTERN)
    stage: Literal[
        "started",
        "collected",
        "checked",
        "reported",
        "backed_up",
        "backup_failed",
        "backup_skipped",
    ]
    at: AwareDatetime
    period: str
    reference: str | None = Field(default=None, description="Clave del registro asociado.")


def is_day(text: str) -> bool:
    try:
        date.fromisoformat(text)
    except ValueError:
        return False
    return bool(_DAY.match(text))


def is_month(text: str) -> bool:
    try:
        date.fromisoformat(f"{text}-01")
    except ValueError:
        return False
    return bool(_MONTH.match(text))


def render_json(model: _Report | CycleStage) -> bytes:
    """Bytes idénticos para el mismo modelo: claves ordenadas, sin reloj."""
    document = model.model_dump(mode="json")
    return (json.dumps(document, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode()
