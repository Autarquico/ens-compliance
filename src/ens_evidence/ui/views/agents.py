"""Panel de agentes: por equipo inscrito, alta, contacto y resultados HOST. Solo lee.

La plantilla recibe únicamente los DTO de este módulo, con campos permitidos: nunca el cuerpo de
una consulta (`QuerySnapshot.data`), sus filas, el uuid del equipo ni el registro de agentes
completo. Las altas, revocaciones y consultas en vivo siguen en la CLI.
"""

from dataclasses import dataclass, field
from datetime import datetime

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import BaseRoute, Route

from ens_evidence.agent.osquery_config import pack_queries
from ens_evidence.agent.registry import AgentRecord, RegistryError, load_registry
from ens_evidence.connectors.config import load_connector_config
from ens_evidence.endpoint.agent_view import AgentView, build_views
from ens_evidence.endpoint.checks import AGENT
from ens_evidence.endpoint.config import CONFIG_NAME, EndpointConfig
from ens_evidence.endpoint.evaluate import evaluate_workspace
from ens_evidence.endpoint.evidence import load_host_evidence
from ens_evidence.models.contracts import CheckResult
from ens_evidence.ui.pages import page_context, render
from ens_evidence.ui.views import format_instant
from ens_evidence.workspace import WorkspaceError, WorkspaceStore

ADMISSION_LABELS = {
    "never": "Sin alta",
    "admitted": "Admitido",
    "revoked": "Revocado",
    "unreadable": "Registro ilegible",
}


@dataclass(frozen=True)
class ContactRow:
    generation: int
    collected_at: str
    evidence_id: str


@dataclass(frozen=True)
class QueryRow:
    query_id: str
    collected_at: str | None
    row_count: int | None
    evidence_id: str | None


@dataclass(frozen=True)
class CheckRow:
    check_id: str
    status: str
    reason: str | None
    reason_code: str | None
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class AgentRow:
    asset: str
    admission: str
    admission_label: str
    generation: int | None
    admitted_at: str
    contact: ContactRow | None
    host002: CheckRow | None


@dataclass(frozen=True)
class AgentDetail:
    row: AgentRow
    queries: list[QueryRow] = field(default_factory=list)
    checks: list[CheckRow] = field(default_factory=list)
    history: list[ContactRow] = field(default_factory=list)


@dataclass(frozen=True)
class Panel:
    rows: dict[str, AgentRow]
    details: dict[str, AgentDetail]
    problems: list[str]


def _admission(
    registry: dict[str, AgentRecord] | None, asset: str
) -> tuple[str, AgentRecord | None]:
    if registry is None:
        return "unreadable", None
    record = registry.get(asset)
    if record is None:
        return "never", None
    return ("revoked" if record.revoked else "admitted"), record


def _contact(view: AgentView, generation: int) -> ContactRow | None:
    latest = view.latest_contact(generation)
    if latest is None:
        return None
    return ContactRow(latest.generation, format_instant(latest.collected_at), latest.evidence_id)


def _history(view: AgentView, current: int | None) -> list[ContactRow]:
    others = sorted(
        (c for c in view.contacts if c.generation != current),
        key=lambda c: (c.collected_at, c.evidence_id),
        reverse=True,
    )
    return [ContactRow(c.generation, format_instant(c.collected_at), c.evidence_id) for c in others]


def _check_row(result: CheckResult) -> CheckRow:
    # Solo el motivo de HOST-002 (fechas y umbral); el de los demás cita valores de las filas.
    reason = result.reason if result.check_id == AGENT else None
    return CheckRow(
        result.check_id, result.status.value, reason, result.reason_code, tuple(result.evidence_ids)
    )


def _row_count(data: dict) -> int | None:
    count = data.get("row_count")
    return count if isinstance(count, int) and not isinstance(count, bool) else None


def _queries(view: AgentView) -> list[QueryRow]:
    rows = []
    for query_id in pack_queries():
        snapshot = view.snapshots.get(query_id)
        if snapshot is None:
            rows.append(QueryRow(query_id, None, None, None))
        else:
            rows.append(
                QueryRow(
                    query_id,
                    format_instant(snapshot.collected_at),
                    _row_count(snapshot.data),
                    snapshot.evidence_id,
                )
            )
    return rows


def _results(store: WorkspaceStore, now: datetime, problems: list[str]) -> list[CheckResult]:
    try:
        return evaluate_workspace(store, now).results
    except WorkspaceError:
        problems.append(
            "No se pudieron evaluar los resultados HOST: revisa el inventario de activos y la "
            "configuración del conector de endpoints."
        )
        return []


def build_panel(store: WorkspaceStore, now: datetime) -> Panel:
    problems: list[str] = []
    try:
        config = load_connector_config(store, CONFIG_NAME, EndpointConfig)
    except WorkspaceError:
        return Panel(
            {},
            {},
            ["No se puede leer connectors/endpoint.yaml: no hay equipos inscritos que mostrar."],
        )
    try:
        registry: dict[str, AgentRecord] | None = load_registry(store).agents
    except RegistryError:
        registry = None
        problems.append(
            "El registro de agentes no se puede leer: no se sabe qué equipos están dados de alta."
        )
    loaded = load_host_evidence(store)
    if loaded.unreadable:
        problems.append(f"Hay {len(loaded.unreadable)} evidencias que no se han podido leer.")
    views = build_views(loaded.agent, config.hosts)
    results = _results(store, now, problems)
    rows: dict[str, AgentRow] = {}
    details: dict[str, AgentDetail] = {}
    for asset in sorted(config.hosts):
        admission, record = _admission(registry, asset)
        view = views.get(asset, AgentView())
        current = record.generation if record and not record.revoked else None
        mine = sorted((r for r in results if r.asset_id == asset), key=lambda r: r.check_id)
        checks = [_check_row(r) for r in mine]
        row = AgentRow(
            asset=asset,
            admission=admission,
            admission_label=ADMISSION_LABELS[admission],
            generation=record.generation if record else None,
            admitted_at=format_instant(record.last_admission_at)
            if record
            else format_instant(None),
            contact=_contact(view, current) if current is not None else None,
            host002=next((c for c in checks if c.check_id == AGENT), None),
        )
        rows[asset] = row
        details[asset] = AgentDetail(row, _queries(view), checks, _history(view, current))
    return Panel(rows, details, problems)


def _panel(request: Request) -> Panel:
    return build_panel(request.app.state.store, request.app.state.clock())


def listing(request: Request) -> Response:
    panel = _panel(request)
    return render(
        request,
        "agents.html",
        **page_context(request, "agentes", "Agentes"),
        rows=list(panel.rows.values()),
        problems=panel.problems,
    )


def detail(request: Request) -> Response:
    panel = _panel(request)
    shown = panel.details.get(request.path_params["activo"])
    if shown is None:
        raise HTTPException(404)
    return render(
        request,
        "agents_detail.html",
        **page_context(request, "agentes", f"Agente {shown.row.asset}"),
        detail=shown,
        problems=panel.problems,
    )


AGENT_ROUTES: list[BaseRoute] = [
    Route("/agentes", listing, methods=["GET"]),
    Route("/agentes/{activo}", detail, methods=["GET"]),
]
