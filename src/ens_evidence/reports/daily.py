"""Constructor del informe diario (guía :907-925). Puro: lee el workspace y no escribe.

Reglas del dato (plan §1): cada valor lleva `sources` o es `Unavailable` con su motivo; lo que no
se pudo obtener nunca se presenta como 0 ni como «sin cambios». Los recuentos por conector salen
del último intento utilizable al corte (`StoredCheckResults`), no de los resúmenes de
`assessment_status`; la preparación y los bloqueos sí salen de `assessment_status`.
"""

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, tzinfo

from ens_evidence import __version__
from ens_evidence.assessment.render import HUMAN_REVIEW_PENDING, NO_CONFORMITY, NO_PRE_AUDIT
from ens_evidence.assessment.status import AssessmentStatus, assessment_status, count_statuses
from ens_evidence.categorization.approval import APPROVAL_KEY as CATEGORIZATION_APPROVAL_KEY
from ens_evidence.checks.catalog import KNOWN_CHECKS
from ens_evidence.checks.inputs import load_endpoint_inputs
from ens_evidence.endpoint.evidence import latest_by_host
from ens_evidence.endpoint.importer import ENDPOINT_ASSET_TYPES
from ens_evidence.evidence.layout import manifest_key
from ens_evidence.incidents.layout import INCIDENTS_PREFIX, StoredRecord
from ens_evidence.incidents.model import IncidentState, IncidentStatus
from ens_evidence.incidents.verify import check_all_incidents
from ens_evidence.matrix.messages import blocker_text
from ens_evidence.matrix.store import MATRIX_KEY
from ens_evidence.matrix.technical import (
    CheckResultsSource,
    CheckRun,
    ConnectorProblems,
    StaticCheckResults,
    latest_run,
    load_checks,
)
from ens_evidence.models.contracts import EvidenceManifest
from ens_evidence.models.enums import CollectionStatus
from ens_evidence.orgdocs.approval import APPROVALS_PREFIX, STAMP, DocumentState, all_statuses
from ens_evidence.orgdocs.keys import document_key
from ens_evidence.reports.changes import changes_since_base
from ens_evidence.reports.expiries import scan_expiries
from ens_evidence.reports.model import (
    AssetsSummary,
    CheckCounts,
    ConnectorEntry,
    CycleInfo,
    DailyReport,
    DocumentEntry,
    ExpiryItem,
    IncidentEntry,
    IncidentRecordRef,
    InterruptedCycle,
    ReadinessValue,
    Section,
    Sourced,
    SourceKind,
    SourceRef,
    Unavailable,
)
from ens_evidence.reports.store import CYCLES_PREFIX, cycle_finished, run_ids, stages_of
from ens_evidence.risk.models import APPROVAL_KEY as RISK_APPROVAL_KEY
from ens_evidence.scope import APPROVAL_KEY as SCOPE_APPROVAL_KEY
from ens_evidence.scope.inventory import ASSETS_KEY, load_assets
from ens_evidence.workspace import WorkspaceError, WorkspaceStore

CRITICAL_ALERTS_PENDING = "alertas críticas: no implementadas todavía (v0.3)"
INCIDENTS_UNREADABLE = "registro de incidentes ilegible"
PROBLEM_TEXT = {
    "check_history_unreadable": "historial de checks ilegible",
    "check_run_missing": "evidencia posterior al último check",
    "check_config_missing": "configuración ausente desde el último check",
    "check_config_changed": "configuración cambiada desde el último check",
    "check_scope_changed": "alcance cambiado desde el último check",
    "check_run_in_future": "ejecución con fecha futura",
    "check_connector_unknown": "conector desconocido",
    "check_evidence_inconsistent": "evidencia incoherente con su manifiesto",
    "check_evidence_unverified": "evidencia sin verificar",
    "evidence_unreadable": "evidencia ilegible",
}
_COLLECTION_SEVERITY = (
    CollectionStatus.SUCCESS,
    CollectionStatus.NOT_SUPPORTED,
    CollectionStatus.PERMISSION_DENIED,
    CollectionStatus.ERROR,
)


def build_daily(
    store: WorkspaceStore,
    *,
    generated_at: datetime,
    period: str,
    tz: tzinfo,
    cycle: CycleInfo | None,
    checks: CheckResultsSource,
) -> DailyReport:
    """El informe del día `period` (fecha local en `tz`) con los datos hasta el corte.

    Corte: `cycle.data_cutoff`, o `generated_at` sin ciclo. Mismos argumentos y mismo workspace
    dan los mismos bytes."""
    cutoff = cycle.data_cutoff if cycle else generated_at
    runs, problems = load_checks(checks, store, cutoff)
    status = assessment_status(store, cutoff, StaticCheckResults(runs, problems=problems))
    configured = {s.connector: s.configured for s in status.connectors}
    connectors = [
        _connector_entry(store, name, configured[name], runs, problems, cutoff)
        for name in sorted(set(KNOWN_CHECKS.values()))
    ]
    totals, partial_notice = _totals(connectors)
    fields = dict(
        generated_at=generated_at,
        product_version=__version__,
        notices=[],
        period=period,
        cycle=cycle,
        assets=_assets(store, cutoff),
        assets_without_agent=_assets_without_agent(store, cutoff),
        checks=totals,
        connectors=connectors,
        readiness=_readiness(store, status, runs, cutoff),
        documents=_documents(store, cutoff),
        expiring=Unavailable(reason="pendiente"),
        critical_alerts=Unavailable(reason=CRITICAL_ALERTS_PENDING),
        open_incidents=_open_incidents(store, cutoff),
        changes=Unavailable(reason="pendiente"),
        interrupted_cycles=_interrupted_cycles(store, cycle, cutoff),
    )
    expiring, expiry_notice = _expiring(store, cutoff, date.fromisoformat(period), tz)
    fields["expiring"] = expiring
    fields["notices"] = _notices(status, partial_notice, expiry_notice)
    draft = DailyReport(**fields)
    return DailyReport(**{**fields, "changes": changes_since_base(store, draft)})


# --- fuentes ------------------------------------------------------------------------------------


def source_ref(store: WorkspaceStore, kind: SourceKind, key: str, at: datetime) -> SourceRef | None:
    """La fuente con la huella de sus bytes actuales; None si el fichero no existe o no se lee."""
    try:
        data = store.read_bytes(key)
    except WorkspaceError:
        return None
    return SourceRef(kind=kind, key=key, sha256=hashlib.sha256(data).hexdigest(), at=at)


def ordered_sources(sources: Iterable[SourceRef | None]) -> list[SourceRef]:
    unique = {(s.kind, s.key): s for s in sources if s is not None}
    return [unique[k] for k in sorted(unique)]


def _sourced[T](value: T, sources: Iterable[SourceRef | None], missing: str) -> Section[T]:
    """El dato con sus fuentes; sin ninguna, `Unavailable`: un valor sin fuente no se publica."""
    found = ordered_sources(sources)
    return Sourced[T](value=value, sources=found) if found else Unavailable(reason=missing)


# --- conectores ---------------------------------------------------------------------------------


def _problem_reason(details: Iterable[str]) -> str:
    return "; ".join(
        f"{PROBLEM_TEXT.get(detail.partition(':')[0], 'checks bloqueados')} ({detail})"
        for detail in sorted(set(details))
    )


def _connector_entry(
    store: WorkspaceStore,
    connector: str,
    configured: bool,
    runs: Iterable[CheckRun],
    problems: ConnectorProblems,
    cutoff: datetime,
) -> ConnectorEntry:
    if blocked := problems.get(connector):
        gone = Unavailable(reason=_problem_reason(blocked))
        return ConnectorEntry(connector=connector, collection=gone, checks=gone)
    run = latest_run(list(runs), connector, cutoff)
    if run is None:
        reason = (
            "sin ejecución de checks al corte"
            if configured
            else "configuración ausente: conector sin configurar"
        )
        gone = Unavailable(reason=reason)
        return ConnectorEntry(connector=connector, collection=gone, checks=gone)
    source = source_ref(store, "check_run", run.run_id, run.finished_at)
    if source is None:
        gone = Unavailable(reason="ejecución de checks ilegible")
        return ConnectorEntry(connector=connector, collection=gone, checks=gone)
    counts = Sourced[CheckCounts](
        value=CheckCounts(counts=count_statuses(run.results)), sources=[source]
    )
    return ConnectorEntry(connector=connector, collection=_collection(run, source), checks=counts)


def _collection(run: CheckRun, source: SourceRef) -> Section[CollectionStatus]:
    if not run.results:
        return Unavailable(reason="la ejecución no registra resultados de recogida")
    worst = max((r.collection_status for r in run.results), key=_COLLECTION_SEVERITY.index)
    return Sourced[CollectionStatus](value=worst, sources=[source])


def _totals(entries: list[ConnectorEntry]) -> tuple[Section[CheckCounts], str | None]:
    usable = [e.checks for e in entries if isinstance(e.checks, Sourced)]
    missing = [e.connector for e in entries if isinstance(e.checks, Unavailable)]
    if not usable:
        return Unavailable(reason="ningún conector tiene una ejecución de checks utilizable"), None
    counts = count_statuses(())
    for section in usable:
        for status, number in section.value.counts.items():
            counts[status] += number
    notice = (
        "Recuentos parciales: sin ejecución utilizable de " + ", ".join(missing) + "."
        if missing
        else None
    )
    totals = Sourced[CheckCounts](
        value=CheckCounts(counts=counts),
        sources=ordered_sources(s for u in usable for s in u.sources),
    )
    return totals, notice


# --- inventario ---------------------------------------------------------------------------------


def _assets(store: WorkspaceStore, cutoff: datetime) -> Section[AssetsSummary]:
    assets, errors = load_assets(store)
    if assets is None or errors:
        return Unavailable(reason=f"inventario de activos ilegible o ausente ({ASSETS_KEY})")
    by_type: dict[str, int] = {}
    for asset in assets.assets:
        by_type[asset.type.value] = by_type.get(asset.type.value, 0) + 1
    summary = AssetsSummary(total=len(assets.assets), by_type=dict(sorted(by_type.items())))
    return _sourced(
        summary,
        [source_ref(store, "inventory", ASSETS_KEY, cutoff)],
        f"{ASSETS_KEY}: no se puede leer",
    )


def _assets_without_agent(store: WorkspaceStore, cutoff: datetime) -> Section[list[str]]:
    assets, errors = load_assets(store)
    if assets is None or errors:
        return Unavailable(reason=f"inventario de activos ilegible o ausente ({ASSETS_KEY})")
    loaded = load_endpoint_inputs(store)
    if loaded.unreadable or loaded.inconsistent:
        return Unavailable(reason="evidencia de endpoints ilegible o incoherente")
    seen = latest_by_host(i.evidence for i in loaded.valid if i.evidence.collected_at <= cutoff)
    expected = sorted(a.id for a in assets.assets if a.type in ENDPOINT_ASSET_TYPES)
    without = [asset_id for asset_id in expected if asset_id not in seen]
    sources = [source_ref(store, "inventory", ASSETS_KEY, cutoff)]
    sources += [
        source_ref(store, "evidence", manifest_key(e.evidence_id), e.collected_at)
        for asset_id, e in seen.items()
        if asset_id in expected
    ]
    return _sourced(without, sources, f"{ASSETS_KEY}: no se puede leer")


# --- preparación --------------------------------------------------------------------------------


def _readiness(
    store: WorkspaceStore, status: AssessmentStatus, runs: Iterable[CheckRun], cutoff: datetime
) -> Section[ReadinessValue]:
    result = status.result
    blockers = [blocker_text(b) for b in result.readiness.blockers]
    blockers += [f"error: {e}" for e in result.errors]
    value = ReadinessValue(readiness=result.readiness.readiness, blockers=blockers)
    records: list[tuple[SourceKind, str]] = [
        ("document", MATRIX_KEY),
        ("scope", SCOPE_APPROVAL_KEY),
        ("approval", CATEGORIZATION_APPROVAL_KEY),
        ("approval", RISK_APPROVAL_KEY),
    ]
    sources = [source_ref(store, kind, key, cutoff) for kind, key in records]
    for connector in sorted(set(KNOWN_CHECKS.values())):
        run = latest_run(list(runs), connector, cutoff)
        if run is not None:
            sources.append(source_ref(store, "check_run", run.run_id, run.finished_at))
    return _sourced(value, sources, "sin registros del workspace de los que derivar la preparación")


def _documents(store: WorkspaceStore, cutoff: datetime) -> Section[list[DocumentEntry]]:
    """Estado de cada documento organizativo (estado actual del workspace). Sin caducidad
    (ADR 0010): aquí solo se informa de si están generados, aprobados u obsoletos."""
    try:
        statuses = all_statuses(store)
    except WorkspaceError:
        return Unavailable(reason="documentos organizativos ilegibles")
    entries: list[DocumentEntry] = []
    sources: list[SourceRef | None] = []
    for status in statuses:
        approval = status.approval
        entries.append(
            DocumentEntry(
                name=status.name,
                state=status.state,
                approved_at=approval.approved_at if approval else None,
                reasons=[*status.reasons],
            )
        )
        if status.state is DocumentState.NOT_GENERATED:
            continue
        sources.append(source_ref(store, "document", document_key(status.name), cutoff))
        if approval is not None:
            stamp = approval.approved_at.astimezone(UTC).strftime(STAMP)
            key = f"{APPROVALS_PREFIX}/{status.name}/{stamp}.yaml"
            sources.append(source_ref(store, "approval", key, approval.approved_at))
    return _sourced(entries, sources, "ningún documento organizativo generado todavía")


def _notices(status: AssessmentStatus, *extra: str | None) -> list[str]:
    notices = [n for n in extra if n]
    if status.pending_human_review:
        notices += [HUMAN_REVIEW_PENDING, NO_PRE_AUDIT]
    return [*notices, NO_CONFORMITY]


# --- incidentes ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class IncidentView:
    """Un incidente del registro al corte: sus registros ya anteriores y sus problemas de
    verificación (que lo dejan fuera de cualquier recuento)."""

    incident_id: str
    problems: tuple[str, ...]
    records: tuple[StoredRecord, ...]
    histories: tuple[EvidenceManifest, ...]

    @property
    def state(self) -> IncidentState:
        return self.records[-1].record.state

    @property
    def broken(self) -> bool:
        return bool(self.problems) or not self.records

    def record_sources(self) -> list[SourceRef]:
        return [
            SourceRef(kind="incident", key=r.key, sha256=r.sha256, at=r.record.recorded_at)
            for r in self.records
        ]


def read_incidents(
    store: WorkspaceStore, *, until: datetime, inclusive: bool
) -> tuple[list[IncidentView], SourceRef] | Unavailable:
    """Los incidentes con registros anteriores a `until` (o iguales si `inclusive`) y la fuente
    de lo consultado: el prefijo `incidents/`, con la huella de su listado de claves."""
    try:
        listing = "\n".join(sorted(store.list(INCIDENTS_PREFIX)))
        checks = check_all_incidents(store)
    except WorkspaceError:
        return Unavailable(reason=f"{INCIDENTS_UNREADABLE} ({INCIDENTS_PREFIX}/)")
    consulted = SourceRef(
        kind="incident",
        key=f"{INCIDENTS_PREFIX}/",
        sha256=hashlib.sha256(listing.encode()).hexdigest(),
        at=until,
    )
    views = []
    for incident_id, check in checks.items():
        records = tuple(
            r
            for r in check.scan.records
            if (r.record.recorded_at <= until if inclusive else r.record.recorded_at < until)
        )
        if records or check.problems:
            views.append(IncidentView(incident_id, check.problems, records, check.scan.histories))
    return views, consulted


def broken_incident(view: IncidentView) -> IncidentEntry:
    problems = view.problems or ("sin registros legibles",)
    return IncidentEntry(id=view.incident_id, problems=sorted(problems))


def _open_incidents(store: WorkspaceStore, cutoff: datetime) -> Section[list[IncidentEntry]]:
    reading = read_incidents(store, until=cutoff, inclusive=True)
    if isinstance(reading, Unavailable):
        return reading
    views, consulted = reading
    entries: list[IncidentEntry] = []
    sources = [consulted]
    for view in views:
        if view.broken:
            entries.append(broken_incident(view))
        elif view.state.status is IncidentStatus.OPEN:
            entries.append(_open_entry(view))
            sources += view.record_sources()
    return Sourced[list[IncidentEntry]](value=entries, sources=ordered_sources(sources))


def _open_entry(view: IncidentView) -> IncidentEntry:
    state, last = view.state, view.records[-1].record
    return IncidentEntry(
        id=view.incident_id,
        title=state.title,
        severity=state.severity,
        detected_at=state.detected_at,
        simulated=state.simulated,
        last_record=IncidentRecordRef(seq=last.seq, kind=last.kind, recorded_at=last.recorded_at),
    )


# --- caducidades y ciclos -----------------------------------------------------------------------


def _expiring(
    store: WorkspaceStore, cutoff: datetime, today: date, tz: tzinfo
) -> tuple[Section[list[ExpiryItem]], str | None]:
    scan = scan_expiries(store, cutoff=cutoff, today=today, tz=tz)
    notice = "Caducidades incompletas: " + "; ".join(scan.problems) + "." if scan.problems else None
    return (
        _sourced(
            list(scan.items),
            [*scan.consulted, *(s for item in scan.items for s in item.sources)],
            "no hay ninguna fuente de caducidades en el workspace",
        ),
        notice,
    )


def _interrupted_cycles(
    store: WorkspaceStore, current: CycleInfo | None, cutoff: datetime
) -> Section[list[InterruptedCycle]]:
    consulted: list[SourceRef | None] = []
    interrupted: list[InterruptedCycle] = []
    unreadable: list[str] = []
    for run_id in run_ids(store):
        reading = stages_of(store, run_id)
        for stage in reading.stages:
            key = f"{CYCLES_PREFIX}/{run_id}.{stage.stage}.json"
            consulted.append(source_ref(store, "cycle", key, stage.at))
        if current is not None and run_id == current.run_id:
            continue
        started = next((s for s in reading.stages if s.stage == "started"), None)
        if cycle_finished(store, run_id):
            continue
        if started is None or reading.problems:
            unreadable.append(run_id)
            continue
        interrupted.append(
            InterruptedCycle(
                run_id=run_id, last_stage=reading.stages[-1].stage, started_at=started.at
            )
        )
    if unreadable:
        return Unavailable(reason="marcadores de ciclo ilegibles: " + ", ".join(unreadable))
    return _sourced(interrupted, consulted, "no hay marcadores de ciclo registrados")
