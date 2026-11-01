"""Constructor del informe mensual (guía :927-941). Puro: lee el workspace y no escribe.

`period` es el mes informado (el mes natural local anterior al día de generación); su ventana se
guarda en UTC. Cada agregado lleva la cobertura de informes diarios del mes y marca `partial` si
faltan días. Lo que el producto aún no registra (incidentes, vulnerabilidades, revisiones de
acceso) es `Unavailable` con su motivo: nunca 0.
"""

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, tzinfo

import yaml
from pydantic import ValidationError

from ens_evidence import __version__
from ens_evidence.assessment.render import HUMAN_REVIEW_PENDING, NO_CONFORMITY, NO_PRE_AUDIT
from ens_evidence.assessment.status import assessment_status, count_statuses
from ens_evidence.backup.records import list_runs, run_key
from ens_evidence.backup.restore_test import RESTORE_TESTS_PREFIX, RestoreTest
from ens_evidence.categorization.approval import APPROVAL_KEY as CATEGORIZATION_APPROVAL_KEY
from ens_evidence.checks.catalog import KNOWN_CHECKS
from ens_evidence.evidence import list_manifests
from ens_evidence.evidence.layout import manifest_key
from ens_evidence.matrix.messages import blocker_text
from ens_evidence.matrix.store import MATRIX_KEY
from ens_evidence.matrix.technical import CheckResultsSource, latest_run, load_checks, subject_of
from ens_evidence.models.enums import CheckStatus
from ens_evidence.orgdocs.approval import DocumentState, all_statuses
from ens_evidence.orgdocs.keys import document_key
from ens_evidence.reports.daily import (
    INCIDENTS_PENDING,
    ordered_sources,
    source_ref,
)
from ens_evidence.reports.expiries import scan_expiries
from ens_evidence.reports.model import (
    Aggregate,
    Coverage,
    CycleInfo,
    Entry,
    MonthlyReport,
    Section,
    Sourced,
    SourceKind,
    SourceRef,
    Unavailable,
    Window,
)
from ens_evidence.reports.store import (
    Canonical,
    PeriodScan,
    load_json_strict,
    pairs,
    reports_in_period,
)
from ens_evidence.risk.models import APPROVAL_KEY as RISK_APPROVAL_KEY
from ens_evidence.risk.models import TREATMENT_PLAN_KEY, ActionStatus, TreatmentPlanFile
from ens_evidence.scope import APPROVAL_KEY as SCOPE_APPROVAL_KEY
from ens_evidence.scope.inventory import ASSETS_KEY, load_assets
from ens_evidence.workspace import WorkspaceError, WorkspaceStore
from ens_evidence.yaml_unique import safe_load_unique

VULNERABILITIES_PENDING = "registro de vulnerabilidades: no implementado todavía"
ACCESS_REVIEWS_PENDING = "revisiones de acceso: no implementadas todavía"
TREATMENT_PLAN_LABEL = "plan de tratamiento de riesgos"
_OPEN_ACTIONS = frozenset({ActionStatus.PENDING, ActionStatus.IN_PROGRESS})
_DEVIATIONS = frozenset({CheckStatus.FAIL, CheckStatus.ERROR})


def month_window(period: str, tz: tzinfo) -> Window:
    """El mes natural local `period` (`YYYY-MM`) como intervalo [inicio, fin) en UTC."""
    first = date.fromisoformat(f"{period}-01")
    following = (first.replace(day=28) + timedelta(days=4)).replace(day=1)

    def utc(day: date) -> datetime:
        return datetime(day.year, day.month, day.day, tzinfo=tz).astimezone(UTC)

    return Window(start=utc(first), end=utc(following))


def previous_month(period: str) -> str:
    first = date.fromisoformat(f"{period}-01")
    last = first - timedelta(days=1)
    return f"{last.year:04d}-{last.month:02d}"


@dataclass(frozen=True)
class _Month:
    store: WorkspaceStore
    generated_at: datetime
    period: str
    tz: tzinfo
    window: Window
    scan: PeriodScan
    checks: CheckResultsSource

    @property
    def coverage(self) -> Coverage:
        return self.scan.coverage

    @property
    def today(self) -> date:
        return self.generated_at.astimezone(self.tz).date()

    def aggregate(
        self,
        sources: list[SourceRef | None],
        missing: str,
        *,
        counts: dict[str, int] | None = None,
        items: list[Entry] | None = None,
        label: str | None = None,
    ) -> Section[Aggregate]:
        """El agregado con sus fuentes; sin ninguna, `Unavailable`."""
        found = ordered_sources(sources)
        if not found:
            return Unavailable(reason=missing)
        value = Aggregate(
            label=label,
            coverage=self.coverage,
            counts=dict(sorted((counts or {}).items())),
            items=sorted(items or [], key=lambda e: (e.subject, e.detail)),
        )
        return Sourced[Aggregate](value=value, sources=found)

    def ref(self, kind: SourceKind, key: str, at: datetime | None = None) -> SourceRef | None:
        return source_ref(self.store, kind, key, at or self.generated_at)

    def in_window(self, instant: datetime) -> bool:
        return self.window.start <= instant < self.window.end


def build_monthly(
    store: WorkspaceStore,
    *,
    generated_at: datetime,
    period: str,
    tz: tzinfo,
    checks: CheckResultsSource,
    cycle: CycleInfo | None = None,
) -> MonthlyReport:
    """El informe del mes `period` (`YYYY-MM`, hora local de `tz`). Mismos argumentos y mismo
    workspace dan los mismos bytes."""
    month = _Month(
        store,
        generated_at,
        period,
        tz,
        month_window(period, tz),
        reports_in_period(store, period),
        checks,
    )
    inventory, inventory_ids = _inventory(month)
    pending, pending_review = _pending(month)
    return MonthlyReport(
        generated_at=generated_at,
        product_version=__version__,
        notices=_notices(month, pending_review),
        period=period,
        window=month.window,
        cycle=cycle,
        inventory=inventory,
        changes=_changes(month, inventory_ids),
        deviations=_deviations(month),
        evidence=_evidence(month),
        incidents=Unavailable(reason=INCIDENTS_PENDING),
        vulnerabilities=Unavailable(reason=VULNERABILITIES_PENDING),
        backups=_backups(month),
        restore_tests=_restore_tests(month),
        access_reviews=Unavailable(reason=ACCESS_REVIEWS_PENDING),
        pending=pending,
        corrective_actions=_corrective_actions(month),
        daily_reports=_daily_reports(month),
    )


def _notices(month: _Month, pending_review: bool) -> list[str]:
    notices = []
    coverage = month.coverage
    if coverage.partial:
        notices.append(
            f"Informe parcial: {coverage.days_with_report} de {coverage.days_in_period} días "
            "con informe diario legible."
        )
    if month.scan.problems:
        notices.append("Informes diarios ilegibles: " + "; ".join(month.scan.problems) + ".")
    if pending_review:
        notices += [HUMAN_REVIEW_PENDING, NO_PRE_AUDIT]
    return [*notices, NO_CONFORMITY]


def _report_ref(day: Canonical) -> SourceRef:
    return SourceRef(
        kind="report",
        key=day.pair.json_key,
        sha256=day.json_sha256,
        at=day.report.generated_at,
    )


# --- inventario y cambios -----------------------------------------------------------------------


def _inventory(month: _Month) -> tuple[Section[Aggregate], dict[str, str] | None]:
    """El inventario actual y, aparte, `{id: tipo}` para comparar con el mensual anterior."""
    assets, errors = load_assets(month.store)
    if assets is None or errors:
        gone = Unavailable(reason=f"inventario de activos ilegible o ausente ({ASSETS_KEY})")
        return gone, None
    by_id = {a.id: a.type.value for a in assets.assets}
    counts = Counter(by_id.values())
    section = month.aggregate(
        [month.ref("inventory", ASSETS_KEY)],
        f"{ASSETS_KEY}: no se puede leer",
        counts={"total": len(by_id), **{f"tipo {t}": n for t, n in counts.items()}},
        items=[Entry(subject=asset_id, detail=kind) for asset_id, kind in by_id.items()],
    )
    return section, by_id


@dataclass(frozen=True)
class _PreviousMonthly:
    report: MonthlyReport
    key: str
    ref: SourceRef


def _previous_monthly(month: _Month) -> _PreviousMonthly | Unavailable:
    """El último par publicado del mes inmediatamente anterior; sin él no hay base."""
    previous = previous_month(month.period)
    published = [p for p in pairs(month.store, "monthly", previous) if p.json_key]
    if not published:
        return Unavailable(reason=f"no hay informe mensual de {previous}")
    last = published[-1]
    if not last.published or last.html_key is None:
        return Unavailable(reason=f"{last.json_key}: par incompleto, falta el HTML")
    try:
        data = month.store.read_bytes(last.json_key)
        report = MonthlyReport.model_validate(load_json_strict(data))
    except (WorkspaceError, ValueError, ValidationError):
        return Unavailable(reason=f"{last.json_key}: ilegible o no válido")
    if report.period != previous:
        return Unavailable(reason=f"{last.json_key}: no coincide con su periodo")
    ref = source_ref(month.store, "report", last.json_key, report.generated_at)
    if ref is None:
        return Unavailable(reason=f"{last.json_key}: ilegible o no válido")
    return _PreviousMonthly(report, last.json_key, ref)


def _inventory_changes(
    month: _Month, current: dict[str, str] | None
) -> tuple[list[Entry], dict[str, int], list[SourceRef]]:
    base = _previous_monthly(month)
    if isinstance(base, Unavailable):
        why = f"altas y bajas del inventario no calculables: {base.reason}"
        return [Entry(subject="inventario", detail=why)], {}, []
    before = base.report.inventory
    if current is None or isinstance(before, Unavailable):
        why = "altas y bajas del inventario no calculables: inventario no disponible"
        return [Entry(subject="inventario", detail=why)], {}, [base.ref]
    old = {e.subject for e in before.value.items}
    added, removed = sorted(set(current) - old), sorted(old - set(current))
    detail = f"frente al informe mensual {base.report.period}"
    items = [Entry(subject=a, detail=f"alta {detail}") for a in added]
    items += [Entry(subject=r, detail=f"baja {detail}") for r in removed]
    return items, {"altas": len(added), "bajas": len(removed)}, [base.ref]


def _changes(month: _Month, current: dict[str, str] | None) -> Section[Aggregate]:
    items, counts, sources = _inventory_changes(month, current)
    daily = 0
    for day, canonical in sorted(month.scan.days.items()):
        changes = canonical.report.changes
        if isinstance(changes, Unavailable):
            continue
        sources.append(_report_ref(canonical))
        for change in changes.value.items:
            daily += 1
            items.append(Entry(subject=change.subject, detail=f"{change.change} · {day}"))
    counts["cambios en informes diarios"] = daily
    return month.aggregate(
        list(sources),
        "sin informes diarios ni mensual anterior en que basar los cambios",
        counts=counts,
        items=items,
    )


# --- desviaciones y evidencia -------------------------------------------------------------------


def _deviations(month: _Month) -> Section[Aggregate]:
    """Resultados FAIL/ERROR del último intento de cada conector al cierre del mes."""
    runs, problems = load_checks(month.checks, month.store, month.window.end)
    items: list[Entry] = []
    sources: list[SourceRef | None] = []
    totals = count_statuses(())
    for connector in sorted(set(KNOWN_CHECKS.values())):
        if blocked := problems.get(connector):
            reason = "; ".join(sorted(set(blocked)))
            items.append(Entry(subject=connector, detail=f"checks no disponibles: {reason}"))
            continue
        run = latest_run(list(runs), connector, month.window.end)
        source = month.ref("check_run", run.run_id, run.finished_at) if run else None
        if run is None or source is None:
            continue
        sources.append(source)
        for status, number in count_statuses(run.results).items():
            totals[status] += number
        items += [
            Entry(subject=f"{connector}: {r.check_id} {subject_of(r)}", detail=r.status.value)
            for r in run.results
            if r.status in _DEVIATIONS
        ]
    counts = {status.value: number for status, number in totals.items()}
    return month.aggregate(
        sources,
        "ningún conector tiene una ejecución de checks utilizable al cierre del mes",
        counts=counts,
        items=items,
    )


def _evidence(month: _Month) -> Section[Aggregate]:
    listing = list_manifests(month.store)
    found = [m for m in listing.manifests if month.in_window(m.collected_at)]
    counts = Counter(f"fuente {m.source}" for m in found)
    unreadable = f" ({len(listing.errors)} manifiestos ilegibles)" if listing.errors else ""
    return month.aggregate(
        [month.ref("evidence", manifest_key(m.id), m.collected_at) for m in found],
        "sin evidencia registrada en la ventana" + unreadable,
        counts={"total": len(found), "manifiestos ilegibles": len(listing.errors), **counts},
    )


# --- copias y pruebas de restauración -----------------------------------------------------------


def _none_in_window(what: str, unreadable: int) -> str:
    return f"sin {what} en la ventana" + (" (hay registros ilegibles)" if unreadable else "")


def _backups(month: _Month) -> Section[Aggregate]:
    runs, unreadable = list_runs(month.store)
    found = [r for r in runs if month.in_window(r.started_at)]
    counts = Counter(f"estado {r.status}" for r in found)
    return month.aggregate(
        [month.ref("backup_run", run_key(r.run_id), r.started_at) for r in found],
        _none_in_window("ejecuciones de copia", len(unreadable)),
        counts={"total": len(found), "registros ilegibles": len(unreadable), **counts},
        items=[
            Entry(subject=r.run_id, detail=f"{r.status} · inicio {r.started_at.isoformat()}")
            for r in found
        ],
    )


def _restore_tests(month: _Month) -> Section[Aggregate]:
    found: list[tuple[str, RestoreTest]] = []
    unreadable = 0
    for key in month.store.list(RESTORE_TESTS_PREFIX):
        try:
            test = RestoreTest.model_validate(safe_load_unique(month.store.read_text(key)))
        except (WorkspaceError, yaml.YAMLError, ValidationError):
            unreadable += 1
            continue
        if month.in_window(test.performed_at):
            found.append((key, test))
    approved = sum(t.review.approved for _, t in found)
    return month.aggregate(
        [month.ref("restore_test", key, t.performed_at) for key, t in found],
        _none_in_window("pruebas de restauración", unreadable),
        counts={
            "total": len(found),
            "revisión aprobada": approved,
            "revisión no aprobada": len(found) - approved,
            "registros ilegibles": unreadable,
        },
        items=[
            Entry(
                subject=f"{t.test_id} ({t.system})",
                detail=(
                    f"restaurado={t.result.restored} · integridad={t.result.integrity_verified}"
                    f" · revisión aprobada={t.review.approved}"
                ),
            )
            for _, t in found
        ],
    )


# --- pendientes y plan de tratamiento -----------------------------------------------------------


def _pending(month: _Month) -> tuple[Section[Aggregate], bool]:
    """Estado al día de generación: bloqueos de preparación, documentos sin aprobar y caducidades
    ya vencidas. Devuelve además si queda revisión humana pendiente."""
    status = assessment_status(month.store, month.generated_at, month.checks)
    blockers = [blocker_text(b) for b in status.result.readiness.blockers]
    blockers += [f"error: {e}" for e in status.result.errors]
    items = [Entry(subject="preparación", detail=b) for b in blockers]
    sources: list[SourceRef | None] = [
        month.ref("document", MATRIX_KEY),
        month.ref("scope", SCOPE_APPROVAL_KEY),
        month.ref("approval", CATEGORIZATION_APPROVAL_KEY),
        month.ref("approval", RISK_APPROVAL_KEY),
    ]
    unapproved = 0
    try:
        documents = all_statuses(month.store)
    except WorkspaceError:
        documents = []
        items.append(Entry(subject="documentos", detail="documentos organizativos ilegibles"))
    for document in documents:
        if document.state is DocumentState.APPROVED:
            continue
        unapproved += 1
        items.append(Entry(subject=f"documento {document.name}", detail=document.state.value))
        if document.state is not DocumentState.NOT_GENERATED:
            sources.append(month.ref("document", document_key(document.name)))
    expiry = scan_expiries(month.store, cutoff=month.generated_at, today=month.today, tz=month.tz)
    expired = [i for i in expiry.items if i.days_left < 0]
    items += [
        Entry(subject=f"{i.kind}: {i.subject}", detail=f"vencida hace {-i.days_left} días")
        for i in expired
    ]
    sources += [*expiry.consulted]
    section = month.aggregate(
        sources,
        "sin registros del workspace de los que derivar los pendientes",
        counts={
            "bloqueos de preparación": len(blockers),
            "documentos sin aprobar": unapproved,
            "caducidades vencidas": len(expired),
        },
        items=items,
    )
    return section, status.pending_human_review


def _corrective_actions(month: _Month) -> Section[Aggregate]:
    if not month.store.exists(TREATMENT_PLAN_KEY):
        return Unavailable(reason=f"{TREATMENT_PLAN_LABEL}: {TREATMENT_PLAN_KEY} no existe")
    try:
        plan = TreatmentPlanFile.model_validate(
            safe_load_unique(month.store.read_text(TREATMENT_PLAN_KEY))
        )
    except (WorkspaceError, yaml.YAMLError, ValidationError):
        return Unavailable(reason=f"{TREATMENT_PLAN_LABEL}: {TREATMENT_PLAN_KEY} ilegible")
    overdue = {a.id for a in plan.actions if a.status in _OPEN_ACTIONS and a.due < month.today}
    by_status = Counter(a.status.value for a in plan.actions)
    return month.aggregate(
        [month.ref("risk", TREATMENT_PLAN_KEY)],
        f"{TREATMENT_PLAN_LABEL}: sin fuente",
        label=TREATMENT_PLAN_LABEL,
        counts={
            "total": len(plan.actions),
            "vencidas": len(overdue),
            **{f"estado {s}": n for s, n in by_status.items()},
        },
        items=[
            Entry(
                subject=a.id,
                detail=(
                    f"{a.status.value} · vence {a.due.isoformat()}"
                    + (" · VENCIDA" if a.id in overdue else "")
                ),
            )
            for a in plan.actions
        ],
    )


# --- informes diarios del mes -------------------------------------------------------------------


def _daily_reports(month: _Month) -> Section[Aggregate]:
    scan = month.scan
    coverage = scan.coverage
    year, number = int(month.period[:4]), int(month.period[5:])
    every_day = [f"{year:04d}-{number:02d}-{d:02d}" for d in range(1, coverage.days_in_period + 1)]
    items = [
        Entry(subject=d, detail="sin informe diario legible")
        for d in every_day
        if d not in scan.days
    ]
    items += [Entry(subject="ilegible", detail=problem) for problem in scan.problems]
    items += [
        Entry(
            subject=day,
            detail=f"varios informes ese día; canónico {c.pair.stamp}; otros: {len(c.others)}",
        )
        for day, c in scan.days.items()
        if c.others
    ]
    return month.aggregate(
        [_report_ref(c) for c in scan.days.values()],
        f"ningún informe diario legible en {month.period} (0 de {coverage.days_in_period} días)",
        counts={
            "días con informe": coverage.days_with_report,
            "días sin informe": coverage.days_in_period - coverage.days_with_report,
            "informes ilegibles": len(scan.problems),
        },
        items=items,
    )
