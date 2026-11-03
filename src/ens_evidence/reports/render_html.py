"""HTML de los informes, generado solo desde el modelo (nunca lee el workspace).

Todo dato externo pasa por `esc()` y va únicamente en nodos de texto: sin JS, sin atributos
derivados de datos, sin `href`/`src` y sin recursos externos. La CSP lo refuerza.
"""

from collections.abc import Callable, Iterable
from datetime import UTC, datetime, tzinfo
from html import escape

from ens_evidence.assessment.render import NO_PRE_AUDIT
from ens_evidence.models.enums import CollectionStatus
from ens_evidence.reports.labels import (
    ASSET_TYPE_LABELS,
    BACKUP_CODE_LABELS,
    BACKUP_OUTCOME_LABELS,
    CHANGE_LABELS,
    COLLECTION_LABELS,
    DOCUMENT_STATE_LABELS,
    EXPIRY_KIND_LABELS,
    PHASE_LABELS,
    READINESS_LABELS,
    STAGE_LABELS,
    plain,
)
from ens_evidence.reports.model import (
    Aggregate,
    AssetsSummary,
    BackupStageResult,
    Changes,
    CheckCounts,
    ConnectorEntry,
    CycleInfo,
    DailyReport,
    DocumentEntry,
    ExpiryItem,
    IncidentEntry,
    InterruptedCycle,
    MonthlyReport,
    ReadinessValue,
    Sourced,
    SourceRef,
    Unavailable,
    Window,
)

CSP = "default-src 'none'; style-src 'unsafe-inline'"
CSS = (
    "body{font-family:sans-serif;margin:2rem;color:#111;background:#fff}"
    "table{border-collapse:collapse;width:100%;margin-bottom:1.5rem}"
    "th,td{border:1px solid #bbb;padding:.3rem .5rem;text-align:left;vertical-align:top}"
    "th{background:#f0f0f0}.warn{border:1px solid #a00;padding:.5rem;margin:.5rem 0}"
    ".na{color:#a00}"
)
UNAVAILABLE = "no disponible"

Row = tuple[str, str]


def esc(value: object) -> str:
    return escape(str(value), quote=True)


def _instant(at: datetime) -> str:
    return at.astimezone(UTC).isoformat()


def _source(ref: SourceRef) -> str:
    return f"{esc(ref.kind)} {esc(ref.key)} sha256:{esc(ref.sha256)} @ {esc(_instant(ref.at))}"


def _row(label: str, value: str) -> str:
    return f"<tr><td>{esc(label)}</td><td>{value}</td></tr>"


def _unavailable_row(label: str, na: Unavailable) -> str:
    return _row(label, f'<span class="na">{UNAVAILABLE}: {esc(na.reason)}</span>')


def _table(title: str, body: str, sources: Iterable[str] = ()) -> str:
    """Las fuentes van una vez, al pie de la tabla; sin fuentes (no disponible) no hay pie."""
    foot = "<br>".join(dict.fromkeys(sources))
    tfoot = f"<tfoot><tr><td>Fuentes</td><td>{foot}</td></tr></tfoot>" if foot else ""
    return (
        f"<section><h2>{esc(title)}</h2><table><thead><tr><th>Dato</th><th>Valor</th></tr></thead>"
        f"<tbody>{body}</tbody>{tfoot}</table></section>"
    )


def _section[T](
    title: str, name: str, section: Sourced[T] | Unavailable, rows: Callable[[T], list[Row]]
) -> str:
    if isinstance(section, Unavailable):
        return _table(title, _unavailable_row(name, section))
    body = "".join(_row(label, esc(value)) for label, value in rows(section.value))
    body = body or _row(name, esc("(sin elementos)"))
    return _table(title, body, (_source(ref) for ref in section.sources))


def _check_rows(value: CheckCounts) -> list[Row]:
    return [(status.value, str(n)) for status, n in sorted(value.counts.items())]


def _list_rows(label: str) -> Callable[[list[str]], list[Row]]:
    return lambda items: [(label, item) for item in items]


def _count_rows(label: str) -> Callable[[list[str]], list[Row]]:
    """El total antes de la lista, para que nadie tenga que contar."""
    return lambda items: [("total", str(len(items)))] + [(label, item) for item in items]


def _assets_rows(value: AssetsSummary) -> list[Row]:
    return [("total", str(value.total))] + [
        (f"tipo {plain(ASSET_TYPE_LABELS, kind)}", str(n))
        for kind, n in sorted(value.by_type.items())
    ]


def _readiness_rows(value: ReadinessValue) -> list[Row]:
    return [("preparación", READINESS_LABELS[value.readiness])] + [
        ("bloqueo", b) for b in value.blockers
    ]


def _expiry_rows(items: list[ExpiryItem]) -> list[Row]:
    return [
        (
            f"{plain(EXPIRY_KIND_LABELS, i.kind)}: {i.subject}",
            f"vence {_instant(i.expires_at)} ({i.days_left} días)",
        )
        for i in items
    ]


def _changes_rows(value: Changes) -> list[Row]:
    changes = [(plain(CHANGE_LABELS, i.change), i.subject) for i in value.items]
    return [("base", value.base_period)] + (
        changes or [("cambios", "sin cambios respecto a la base")]
    )


def _interrupted_rows(items: list[InterruptedCycle]) -> list[Row]:
    return [
        (
            i.run_id,
            f"última etapa {plain(STAGE_LABELS, i.last_stage)}; inicio {_instant(i.started_at)}",
        )
        for i in items
    ]


def _backup_rows(value: BackupStageResult) -> list[Row]:
    result = plain(BACKUP_OUTCOME_LABELS, value.outcome)
    if value.code:
        result += f": {plain(BACKUP_CODE_LABELS, value.code)}"
    rows = [(f"ciclo {value.run_id}", f"{result}; fin {_instant(value.finished_at)}")]
    if value.configuration_error:
        rows.append(("fallo de configuración", "la copia está activada y no hay ajustes de copia"))
    return rows


def _incident_rows(items: list[IncidentEntry]) -> list[Row]:
    def detail(item: IncidentEntry) -> str:
        if item.problems:
            return "NO VERIFICADO: " + "; ".join(item.problems)
        last = item.last_record
        assert item.detected_at and last  # un incidente verificado lleva todos sus valores
        return (
            f"{'simulacro' if item.simulated else 'incidente'} · {item.title}"
            f" · severidad {item.severity or 'no declarada'}"
            f" · detectado {_instant(item.detected_at)}"
            f" · último registro {last.seq} ({last.kind.value}) {_instant(last.recorded_at)}"
        )

    return [(item.id, detail(item)) for item in items]


def _aggregate_rows(value: Aggregate) -> list[Row]:
    cov = value.coverage
    rows = [("rótulo", value.label)] if value.label else []
    rows += [
        (
            "cobertura",
            f"{cov.days_with_report}/{cov.days_in_period} días"
            + (" (PARCIAL: faltan informes diarios)" if cov.partial else ""),
        )
    ]
    rows += [(name, str(n)) for name, n in sorted(value.counts.items())]
    return rows + [(e.subject, e.detail) for e in value.items]


def _documents_rows(items: list[DocumentEntry]) -> list[Row]:
    def detail(item: DocumentEntry) -> str:
        parts = [plain(DOCUMENT_STATE_LABELS, item.state.value)]
        if item.approved_at:
            parts.append(f"aprobado {_instant(item.approved_at)}")
        return "; ".join(parts + item.reasons)

    return [(item.name, detail(item)) for item in items]


def _window(window: Window, tz: tzinfo | None) -> str:
    rows = []
    for label, at in (("inicio", window.start), ("fin (excluido)", window.end)):
        local = f"{at.astimezone(tz).isoformat()} / " if tz else ""
        rows.append(_row(label, esc(f"{local}{_instant(at)} UTC")))
    return _table("Ventana", "".join(rows), ["calendario"])


def _cycle(cycle: CycleInfo | None) -> str:
    if cycle is None:
        row = _row("ciclo", esc(f"{UNAVAILABLE}: informe generado fuera de un ciclo"))
        return _table("Ciclo", row)
    rows = [
        ("ciclo", cycle.run_id),
        ("periodo del ciclo", cycle.period),
        ("hora prevista", _instant(cycle.due_at)),
        ("inicio", _instant(cycle.started_at)),
        ("corte de datos", _instant(cycle.data_cutoff)),
    ] + [
        (f"intentos {a.connector}", f"{a.attempts} (último: {_collection(a.last_status)})")
        for a in cycle.connector_attempts
    ]
    if cycle.resumed:
        rows.append(("reanudado", "sí: continúa un intento anterior del mismo ciclo"))
    rows += [
        (f"heredado: {plain(STAGE_LABELS, s.stage)}", _instant(s.at))
        for s in cycle.inherited_stages
    ]
    body = "".join(_row(name, esc(value)) for name, value in rows)
    return _table("Ciclo", body, [esc(f"ciclo {cycle.trigger}"), "calendario", "reloj del ciclo"])


def _collection(status: CollectionStatus | None) -> str:
    return plain(COLLECTION_LABELS, status.value) if status else UNAVAILABLE


def _notices(notices: list[str]) -> str:
    """Solo lo que dice el modelo."""
    items = [n[:1].upper() + n[1:] if n == NO_PRE_AUDIT else n for n in notices]
    return "".join(f'<p class="warn">{esc(n)}</p>' for n in items)


def _document(title: str, report: DailyReport | MonthlyReport, sections: list[str]) -> str:
    return (
        '<!DOCTYPE html><html lang="es"><head><meta charset="utf-8">'
        f'<meta http-equiv="Content-Security-Policy" content="{CSP}">'
        f"<title>{esc(title)}</title><style>{CSS}</style></head><body>"
        f"<h1>{esc(title)}</h1>{_notices(report.notices)}"
        f"<p>Generado: {esc(_instant(report.generated_at))} · versión {esc(report.product_version)}"
        f" · esquema {esc(report.schema_version)}</p>"
        f"{_cycle(report.cycle)}{''.join(sections)}</body></html>\n"
    )


def _collection_text(entry: ConnectorEntry, status: CollectionStatus) -> str:
    """`phase` y `error_type` pueden faltar (informes anteriores): entonces solo el estado."""
    text = _collection(status)
    if entry.phase:
        text += f" · fase: {plain(PHASE_LABELS, entry.phase)}"
    if entry.error_type:
        text += f" (excepción {entry.error_type})"
    return text


def _connectors(report: DailyReport) -> str:
    if not report.connectors:
        return _section(
            "Conectores",
            "conectores",
            Unavailable(reason="sin conectores configurados en el informe"),
            lambda _: [],
        )
    parts = []
    for entry in report.connectors:
        parts.append(
            _section(
                f"Conector {entry.connector}: recogida",
                "recogida",
                entry.collection,
                lambda status, entry=entry: [("estado", _collection_text(entry, status))],
            )
        )
        parts.append(
            _section(f"Conector {entry.connector}: checks", "checks", entry.checks, _check_rows)
        )
    return "".join(parts)


def render_daily_html(report: DailyReport) -> str:
    sections = [
        _section("Activos", "activos", report.assets, _assets_rows),
        _section(
            "Activos sin agente",
            "activos sin agente",
            report.assets_without_agent,
            _count_rows("sin agente"),
        ),
        _section("Checks", "checks", report.checks, _check_rows),
        _connectors(report),
        _section("Preparación", "preparación", report.readiness, _readiness_rows),
        _section("Documentos", "documentos", report.documents, _documents_rows),
        _section("Caducidades", "caducidades", report.expiring, _expiry_rows),
        _section(
            "Alertas críticas", "alertas críticas", report.critical_alerts, _list_rows("alerta")
        ),
        _section(
            "Incidentes abiertos",
            "incidentes abiertos",
            report.open_incidents,
            _incident_rows,
        ),
        _section("Cambios", "cambios", report.changes, _changes_rows),
        _section(
            "Ciclos interrumpidos",
            "ciclos interrumpidos",
            report.interrupted_cycles,
            _interrupted_rows,
        ),
        _section("Copia de seguridad", "copia de seguridad", report.backup, _backup_rows),
    ]
    return _document(f"Informe diario {report.period}", report, sections)


_MONTHLY_SECTIONS = (
    ("inventory", "Inventario"),
    ("changes", "Cambios"),
    ("deviations", "Desviaciones"),
    ("evidence", "Evidencias"),
    ("incidents", "Incidentes"),
    ("vulnerabilities", "Vulnerabilidades"),
    ("backups", "Copias de seguridad"),
    ("restore_tests", "Pruebas de restauración"),
    ("access_reviews", "Revisiones de acceso"),
    ("pending", "Pendientes"),
    ("corrective_actions", "Plan de tratamiento de riesgos (acciones correctivas)"),
    ("daily_reports", "Informes diarios del mes"),
)


def render_monthly_html(report: MonthlyReport, tz: tzinfo | None = None) -> str:
    """`tz`: zona del informe; si se da, la ventana se muestra también en hora local."""
    sections = [_window(report.window, tz)] + [
        _section(title, title.lower(), getattr(report, name), _aggregate_rows)
        for name, title in _MONTHLY_SECTIONS
    ]
    return _document(f"Informe mensual {report.period}", report, sections)
