"""HTML de los informes, generado solo desde el modelo (nunca lee el workspace).

Todo dato externo pasa por `esc()` y va únicamente en nodos de texto: sin JS, sin atributos
derivados de datos, sin `href`/`src` y sin recursos externos. La CSP lo refuerza.
"""

from collections.abc import Callable, Iterable
from datetime import UTC, datetime, tzinfo
from html import escape

from ens_evidence.reports.model import (
    Aggregate,
    AssetsSummary,
    Changes,
    CheckCounts,
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
HUMAN_REVIEW_PENDING = (
    "Revisión humana pendiente: este informe no sustituye la revisión de una persona "
    "ni declara conformidad."
)
UNAVAILABLE = "no disponible"

Row = tuple[str, str]


def esc(value: object) -> str:
    return escape(str(value), quote=True)


def _instant(at: datetime) -> str:
    return at.astimezone(UTC).isoformat()


def _source(ref: SourceRef) -> str:
    return f"{esc(ref.kind)} {esc(ref.key)} sha256:{esc(ref.sha256)} @ {esc(_instant(ref.at))}"


def _sources_cell(sources: Iterable[SourceRef]) -> str:
    return "<br>".join(_source(ref) for ref in sources)


def _row(label: str, value: str, source_cell: str) -> str:
    return f"<tr><td>{esc(label)}</td><td>{value}</td><td>{source_cell}</td></tr>"


def _unavailable_row(label: str, na: Unavailable) -> str:
    reason = f'<span class="na">{UNAVAILABLE}: {esc(na.reason)}</span>'
    return _row(label, reason, f"{UNAVAILABLE}: {esc(na.reason)}")


def _section[T](
    title: str, label: str, section: Sourced[T] | Unavailable, rows: Callable[[T], list[Row]]
) -> str:
    if isinstance(section, Unavailable):
        body = _unavailable_row(label, section)
    else:
        cell = _sources_cell(section.sources)
        body = "".join(_row(name, esc(value), cell) for name, value in rows(section.value))
        body = body or _row(label, esc("(sin elementos)"), cell)
    return (
        f"<section><h2>{esc(title)}</h2><table><thead><tr><th>Dato</th><th>Valor</th>"
        f"<th>Fuente</th></tr></thead><tbody>{body}</tbody></table></section>"
    )


def _check_rows(value: CheckCounts) -> list[Row]:
    return [(status.value, str(n)) for status, n in sorted(value.counts.items())]


def _list_rows(label: str) -> Callable[[list[str]], list[Row]]:
    return lambda items: [(label, item) for item in items]


def _assets_rows(value: AssetsSummary) -> list[Row]:
    return [("total", str(value.total))] + [
        (f"tipo {kind}", str(n)) for kind, n in sorted(value.by_type.items())
    ]


def _readiness_rows(value: ReadinessValue) -> list[Row]:
    return [("preparación", value.readiness.value)] + [("bloqueo", b) for b in value.blockers]


def _expiry_rows(items: list[ExpiryItem]) -> list[Row]:
    return [
        (f"{i.kind}: {i.subject}", f"vence {_instant(i.expires_at)} ({i.days_left} días)")
        for i in items
    ]


def _changes_rows(value: Changes) -> list[Row]:
    return [("base", value.base_period)] + [(i.change, i.subject) for i in value.items]


def _interrupted_rows(items: list[InterruptedCycle]) -> list[Row]:
    return [
        (i.run_id, f"última etapa {i.last_stage}; inicio {_instant(i.started_at)}") for i in items
    ]


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
        parts = [item.state.value]
        if item.approved_at:
            parts.append(f"aprobado {_instant(item.approved_at)}")
        return "; ".join(parts + item.reasons)

    return [(item.name, detail(item)) for item in items]


def _table(title: str, body: str) -> str:
    return (
        f"<section><h2>{esc(title)}</h2><table><thead><tr><th>Dato</th><th>Valor</th>"
        f"<th>Fuente</th></tr></thead><tbody>{body}</tbody></table></section>"
    )


def _window(window: Window, tz: tzinfo | None) -> str:
    rows = []
    for label, at in (("inicio", window.start), ("fin (excluido)", window.end)):
        local = f"{at.astimezone(tz).isoformat()} / " if tz else ""
        rows.append(_row(label, esc(f"{local}{_instant(at)} UTC"), "calendario"))
    return _table("Ventana", "".join(rows))


def _cycle(cycle: CycleInfo | None) -> str:
    if cycle is None:
        rows = [("ciclo", f"{UNAVAILABLE}: informe generado fuera de un ciclo", UNAVAILABLE)]
    else:
        rows = [
            ("ciclo", cycle.run_id, esc(cycle.trigger)),
            ("periodo", cycle.period, "calendario"),
            ("hora prevista", _instant(cycle.due_at), "calendario"),
            ("inicio", _instant(cycle.started_at), "reloj del ciclo"),
            ("corte de datos", _instant(cycle.data_cutoff), "reloj del ciclo"),
        ] + [
            (
                f"intentos {a.connector}",
                f"{a.attempts} (último: {a.last_status.value if a.last_status else UNAVAILABLE})",
                "ciclo",
            )
            for a in cycle.connector_attempts
        ]
    body = "".join(_row(label, esc(value), source) for label, value, source in rows)
    return _table("Ciclo", body)


def _notices(notices: list[str]) -> str:
    items = [*notices, HUMAN_REVIEW_PENDING]
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
                lambda status: [("estado", status.value)],
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
            _list_rows("sin agente"),
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
