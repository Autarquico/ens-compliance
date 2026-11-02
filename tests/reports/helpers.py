import hashlib
from datetime import UTC, datetime

from ens_evidence.assessment.render import NO_CONFORMITY
from ens_evidence.models.enums import CheckStatus, Readiness
from ens_evidence.reports.model import (
    Aggregate,
    CheckCounts,
    Coverage,
    CycleInfo,
    DailyReport,
    MonthlyReport,
    ReadinessValue,
    Sourced,
    SourceRef,
    Unavailable,
    Window,
)
from ens_evidence.reports.model import render_json as render_model
from ens_evidence.reports.store import published_hashes, save_pair, write_stage

SHA = "a" * 64
NOON = datetime(2026, 11, 1, 12, tzinfo=UTC)


def ref(key: str = "evidence/checks/run.json") -> SourceRef:
    return SourceRef(kind="check_run", key=key, sha256=SHA, at=NOON)


def daily(period: str = "2026-11-01", **overrides) -> DailyReport:
    gone = Unavailable(reason="no disponible")
    fields = dict(
        generated_at=NOON,
        product_version="0.1.0",
        notices=[NO_CONFORMITY],
        period=period,
        cycle=None,
        assets=gone,
        assets_without_agent=gone,
        checks=Sourced[CheckCounts](
            value=CheckCounts(counts={CheckStatus.PASS: 2}), sources=[ref()]
        ),
        connectors=[],
        readiness=Sourced[ReadinessValue](
            value=ReadinessValue(readiness=Readiness.NOT_READY, blockers=["x"]), sources=[ref()]
        ),
        documents=gone,
        expiring=gone,
        critical_alerts=gone,
        open_incidents=gone,
        changes=gone,
        interrupted_cycles=gone,
        backup=gone,
    )
    return DailyReport(**{**fields, **overrides})


def monthly(period: str = "2026-10", **overrides) -> MonthlyReport:
    gone = Unavailable(reason="no disponible")
    names = (
        "inventory changes deviations evidence incidents vulnerabilities backups restore_tests "
        "access_reviews pending corrective_actions daily_reports"
    ).split()
    fields = dict(
        generated_at=NOON,
        product_version="0.1.0",
        notices=[NO_CONFORMITY],
        period=period,
        window=Window(
            start=datetime(2026, 9, 30, 22, tzinfo=UTC), end=datetime(2026, 10, 31, 23, tzinfo=UTC)
        ),
        cycle=None,
    )
    fields.update({n: gone for n in names})
    fields["inventory"] = Sourced[Aggregate](
        value=Aggregate(coverage=Coverage.of(30, 31)), sources=[ref()]
    )
    return MonthlyReport(**{**fields, **overrides})


def cycle_info(period: str, at: datetime) -> CycleInfo:
    return CycleInfo(
        run_id=f"run-{period}-{at:%Y%m%dT%H%M%S%f}",
        trigger="scheduled",
        period=period,
        due_at=at,
        started_at=at,
        data_cutoff=at,
    )


def put_manual_daily(store, report: DailyReport, at: datetime, html: bytes = b"<html></html>"):
    """Un par sin ciclo ni marcador: lo que deja `report daily` a mano."""
    return save_pair(store, "daily", report.period, render_model(report), html, at=at)


def put_daily(store, report: DailyReport, at: datetime, html: bytes = b"<html></html>"):
    """Un par publicado por un ciclo: con su `cycle` y su marcador `reported` con las huellas."""
    return _put_published(store, "daily", report, at, html)


def put_monthly(store, report: MonthlyReport, at: datetime, html: bytes = b"<html></html>"):
    """Lo mismo para un mensual: sin marcador de ciclo no sirve de base del siguiente."""
    return _put_published(store, "monthly", report, at, html)


def _put_published(store, kind, report, at, html):
    if report.cycle is None:
        report = report.model_copy(update={"cycle": cycle_info(report.period, at)})
    pair = save_pair(store, kind, report.period, render_model(report), html, at=at)
    json_sha256, html_sha256 = published_hashes(store, pair.json_key)
    write_stage(
        store,
        report.cycle.run_id,
        "reported",
        at=at,
        period=report.period,
        reference=pair.json_key,
        json_sha256=json_sha256,
        html_sha256=html_sha256,
    )
    return pair


def source_sha256(store, key: str) -> str:
    """La huella real de una fuente: sus bytes, o el listado de claves si es un prefijo (`x/`)."""
    if key.endswith("/"):
        return hashlib.sha256("\n".join(sorted(store.list(key.rstrip("/")))).encode()).hexdigest()
    return hashlib.sha256(store.read_bytes(key)).hexdigest()
