"""Inventario de activos: listado, ficha, alta, edición y baja, y reconciliación con los agentes.

Las escrituras usan las mismas APIs que la CLI (`save_asset`, `remove_asset`) y la validación del
paso «activos» del asistente. La plantilla recibe solo los DTO de este módulo: nunca el YAML, un
`CheckResult` (su `reason` puede citar valores), un `AgentRecord` ni el uuid de un equipo.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote, urlencode

from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import BaseRoute, Route

from ens_evidence.agent.registry import RegistryError, load_registry
from ens_evidence.checks.store import RunScan, scan_runs
from ens_evidence.connectors.config import connector_config_key, load_connector_config
from ens_evidence.endpoint.agent_view import AgentView, build_views
from ens_evidence.endpoint.config import CONFIG_NAME, EndpointConfig
from ens_evidence.endpoint.evidence import load_host_evidence
from ens_evidence.scope import (
    Asset,
    AssetType,
    Deployment,
    DiscrepancyKind,
    ExclusionKind,
    ObservedHost,
    ObservedHosts,
    ScopeDocuments,
    ScopeState,
    SystemScope,
    load_documents,
    reconcile,
    scope_status,
)
from ens_evidence.scope.edit import (
    ScopeChoice,
    ScopeEditRejected,
    remove_asset,
    save_asset,
)
from ens_evidence.scope.models import Dependency, Service
from ens_evidence.scope.reconcile import RECONCILED_TYPES, normalize_hostname
from ens_evidence.ui.audit import audit_write
from ens_evidence.ui.forms import Form, FormInvalid, FormTooLarge, first, read_form
from ens_evidence.ui.pages import (
    STATUS_TEXT,
    error_page,
    identity_of,
    page_context,
    permissions_of,
    render,
)
from ens_evidence.ui.store import UiStore
from ens_evidence.ui.views import format_instant
from ens_evidence.ui.views.check_history import CheckRow as CheckRow
from ens_evidence.ui.views.check_history import (
    ConnectorResults,
    EvidenceSummary,
    detail_results,
    latest_results,
    summary,
    unreadable_notes,
)
from ens_evidence.ui.views.matrix import plain
from ens_evidence.ui.wizard.base import StepRejected
from ens_evidence.ui.wizard.scope import (
    ASSET_TYPE_LABELS,
    FIELD_LABELS,
    asset_form_fields,
    asset_form_values,
    asset_summary,
)
from ens_evidence.workspace import WorkspaceError

STEP = "inventario"
LIST_PATH = "/inventario"
NEW_PATH = "/inventario/alta"
ASSET_PATH = "/inventario/activos"


DEPLOYMENT_LABELS = {Deployment.DEPLOYED: "Desplegado", Deployment.PLANNED: "Planificado"}
SCOPE_LABELS = {
    "included": "En el alcance",
    "excluded": "Excluido",
    "undecided": "Sin decidir",
    "unknown": "No se puede leer el alcance",
}
STATUS_LABELS = {code: f"{text} ({code})" for code, (_, text) in STATUS_TEXT.items()}
NO_EVIDENCE = "sin-evidencia"

NO_AGENTS_NOTE = "No hay agentes configurados: no hay equipos que comparar con el inventario."
SAVED_TEXTS = {
    "nuevo": "Activo dado de alta.",
    "editar": "Cambios guardados.",
    "baja": "Activo dado de baja.",
    "sin-cambios": "No había nada que cambiar: no se ha guardado nada.",
}
REVOKED_NOTICE = (
    "La aprobación del alcance se ha revocado. Vuelve a aprobarla en el asistente, "
    "en el paso «Aprobación del alcance»."
)
BLOCKED_NOTICE = (
    "No se guarda nada mientras assets.yaml y system.yaml no sean válidos: corrígelos primero."
)
ACCEPTANCE_REQUIRED = (
    "Confirma que entiendes que guardar revoca la aprobación del alcance y que habrá que "
    "aprobarla de nuevo."
)
REMOVAL_UNKNOWN = (
    "No se pueden leer los servicios o las dependencias: sin ellos no se puede comprobar "
    "que nada use este activo."
)
REMOVAL_REFERENCED = (
    "Hay servicios o dependencias que usan este activo. Quítalos antes en el asistente, en los "
    "pasos «Servicios» y «Dependencias»."
)


# --- DTO -----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Choice:
    value: str
    label: str


@dataclass(frozen=True)
class AssetRow:
    id: str
    href: str
    type: str
    type_label: str
    deployment: str
    deployment_label: str
    scope: str  # included | excluded | undecided | unknown
    scope_label: str
    justification: str | None
    summary: str | None
    evidence: EvidenceSummary | None
    agent_href: str | None


@dataclass(frozen=True)
class RefRow:
    kind: str  # "service" | "dependency"
    id: str
    label: str


@dataclass(frozen=True)
class AssetDetail:
    row: AssetRow
    fields: tuple[tuple[str, str], ...]
    services: tuple[RefRow, ...]
    dependencies: tuple[RefRow, ...]
    results: tuple[ConnectorResults, ...]
    referenced: bool
    references_known: bool  # False si servicios o dependencias no se pueden leer
    removal_blockers: tuple[str, ...]
    removable: bool  # sin bloqueos y con el inventario válido


@dataclass(frozen=True)
class ReconcileRow:
    kind: str
    kind_label: str
    asset_id: str
    hostname: str | None  # el de connectors/endpoint.yaml
    inventory_hostname: str | None
    hostname_mismatch: bool
    detail: str | None
    prefill_href: str | None
    asset_href: str | None


@dataclass(frozen=True)
class Reconciliation:
    rows: tuple[ReconcileRow, ...] = ()
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Filters:
    tipo: str = ""
    despliegue: str = ""
    alcance: str = ""
    estado: str = ""


@dataclass(frozen=True)
class FilterOptions:
    tipo: tuple[Choice, ...]
    despliegue: tuple[Choice, ...]
    alcance: tuple[Choice, ...]
    estado: tuple[Choice, ...]


@dataclass(frozen=True)
class InventoryPage:
    rows: tuple[AssetRow, ...]
    total: int  # antes de filtrar
    filters: Filters
    filter_options: FilterOptions
    file_errors: tuple[str, ...]
    integrity_warnings: tuple[str, ...]
    writes_blocked: bool
    reconciliation: Reconciliation
    history_notes: tuple[str, ...]
    generated_at: str


@dataclass(frozen=True)
class ApprovalWarning:
    approved_by: str
    approved_at: str


@dataclass(frozen=True)
class FormField:
    name: str  # el nombre completo del campo del formulario («asset_os»)
    label: str
    kind: str  # "text" | "bool"
    required: bool


@dataclass(frozen=True)
class AssetForm:
    mode: str  # "nuevo" | "editar"
    action: str
    cancel_href: str
    asset_id: str  # vacío en el alta
    type: str | None  # None: aún no se ha elegido el tipo
    type_label: str | None
    fields: tuple[FormField, ...]
    asset_types: tuple[Choice, ...]
    deployment: str
    scope: str  # "included" | "excluded" | "" (activo sin decidir; no se toca si no se elige)
    justification: str
    approval_warning: ApprovalWarning | None


@dataclass(frozen=True)
class Removal:
    asset_id: str
    action: str
    cancel_href: str
    blockers: tuple[str, ...]  # si hay alguno, no se ofrece el formulario
    references: tuple[RefRow, ...]
    approval_warning: ApprovalWarning | None


# --- lo que se lee en cada petición --------------------------------------------------------------


@dataclass(frozen=True)
class Endpoint:
    config: EndpointConfig | None
    absent: bool
    problem: str | None


@dataclass(frozen=True)
class Loaded:
    docs: ScopeDocuments
    scan: RunScan
    endpoint: Endpoint
    now: datetime
    admitted: frozenset[str]  # con alta vigente en el registro de agentes (no revocada)

    @property
    def writes_blocked(self) -> bool:
        return bool(self.docs.assets.errors or self.docs.system.errors)

    @property
    def assets(self) -> list[Asset]:
        model = self.docs.assets.model
        return model.assets if model else []


def _endpoint(store: UiStore) -> Endpoint:
    if not store.exists(connector_config_key(CONFIG_NAME)):
        return Endpoint(None, True, None)
    try:
        return Endpoint(load_connector_config(store, CONFIG_NAME, EndpointConfig), False, None)
    except WorkspaceError:
        return Endpoint(
            None, False, "No se puede leer connectors/endpoint.yaml: no se comparan los equipos."
        )


def _admitted(store: UiStore, endpoint: Endpoint) -> frozenset[str]:
    if endpoint.config is None:
        return frozenset()
    try:
        registry = load_registry(store).agents
    except RegistryError:
        return frozenset()
    return frozenset(i for i, r in registry.items() if i in endpoint.config.hosts and not r.revoked)


def _load(request: Request) -> Loaded:
    store, now = request.app.state.store, request.app.state.clock()
    endpoint = _endpoint(store)
    return Loaded(
        load_documents(store), scan_runs(store), endpoint, now, _admitted(store, endpoint)
    )


def _quoted(asset_id: str) -> str:
    return quote(asset_id, safe="")


def _href(asset_id: str) -> str:
    return f"{ASSET_PATH}/{_quoted(asset_id)}"


def _errors(messages: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(plain(m) for m in messages))


# --- filas y ficha -------------------------------------------------------------------------------


def _placements(system: SystemScope | None) -> dict[str, tuple[str, str | None]]:
    if system is None:
        return {}
    placed = {ref: ("included", None) for ref in system.included_assets}
    for exclusion in system.exclusions:
        if exclusion.kind is ExclusionKind.ASSET:
            placed[exclusion.ref] = ("excluded", exclusion.justification)
    return placed


def _rows(loaded: Loaded) -> list[AssetRow]:
    placements = _placements(loaded.docs.system.model)
    unknown = loaded.docs.system.model is None
    evidence = latest_results(loaded.scan)
    rows = []
    for asset in loaded.assets:
        scope, justification = placements.get(
            asset.id, ("unknown" if unknown else "undecided", None)
        )
        contributions = evidence.get(asset.id)
        rows.append(
            AssetRow(
                id=asset.id,
                href=_href(asset.id),
                type=asset.type.value,
                type_label=ASSET_TYPE_LABELS[asset.type],
                deployment=asset.deployment.value,
                deployment_label=DEPLOYMENT_LABELS[asset.deployment],
                scope=scope,
                scope_label=SCOPE_LABELS[scope],
                justification=justification,
                summary=asset_summary(asset.model_dump(exclude_none=True)),
                evidence=summary(loaded.scan, contributions) if contributions else None,
                agent_href=f"/agentes/{_quoted(asset.id)}" if asset.id in loaded.admitted else None,
            )
        )
    return rows


def _display(value: Any) -> str:
    if isinstance(value, bool):
        return "Sí" if value else "No"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def _fields(asset: Asset) -> tuple[tuple[str, str], ...]:
    shown = asset.model_dump(exclude_none=True, exclude={"id", "type", "deployment"})
    return tuple((FIELD_LABELS.get(name, name), _display(value)) for name, value in shown.items())


def _service_ref(service: Service) -> RefRow:
    return RefRow("service", service.id, service.name)


def _dependency_ref(dependency: Dependency) -> RefRow:
    label = f"{dependency.asset} depende de {dependency.depends_on}"
    return RefRow(
        "dependency",
        dependency.id,
        f"{label} ({dependency.purpose})" if dependency.purpose else label,
    )


def _detail(loaded: Loaded, row: AssetRow, asset: Asset) -> AssetDetail:
    services_doc, deps_doc = loaded.docs.services, loaded.docs.dependencies
    known = all(not d.errors or d.absent for d in (services_doc, deps_doc))
    services = tuple(
        _service_ref(s)
        for s in (services_doc.model.services if services_doc.model else [])
        if asset.id in s.assets
    )
    dependencies = tuple(
        _dependency_ref(d)
        for d in (deps_doc.model.dependencies if deps_doc.model else [])
        if asset.id in (d.asset, d.depends_on)
    )
    referenced = bool(services or dependencies)
    blockers = (
        *((REMOVAL_UNKNOWN,) if not known else ()),
        *((REMOVAL_REFERENCED,) if referenced else ()),
    )
    contributions = latest_results(loaded.scan).get(asset.id, [])
    return AssetDetail(
        row=row,
        fields=_fields(asset),
        services=services,
        dependencies=dependencies,
        results=detail_results(contributions),
        referenced=referenced,
        references_known=known,
        removal_blockers=blockers,
        removable=not blockers and not loaded.writes_blocked,
    )


# --- reconciliación con los agentes (K5) ---------------------------------------------------------

KIND_LABELS = {
    "not_inventoried": "Reporta y no está inventariado",
    "not_inventoried_silent": "Admitido, sin contacto reciente y no está inventariado",
    "admitted_silent": "Admitido y sin contacto reciente",
    "hostname_mismatch": "El nombre del equipo no coincide con el del inventario",
    "not_enrolled": "Desplegado en el inventario y sin alta en el registro de agentes",
}
_KIND_ORDER = {kind: index for index, kind in enumerate(KIND_LABELS)}


def _prefill(asset_id: str, hostname: str) -> str:
    query = urlencode({"tipo": AssetType.LAPTOP.value, "id": asset_id, "hostname": hostname})
    return f"{NEW_PATH}?{query}"


def build_reconciliation(store: UiStore, loaded: Loaded) -> Reconciliation:
    """Compara el inventario con los equipos admitidos y su último contacto. Solo informa."""
    endpoint, assets = loaded.endpoint, loaded.docs.assets.model
    if endpoint.absent:
        return Reconciliation(notes=(NO_AGENTS_NOTE,))
    if endpoint.config is None:
        return Reconciliation(notes=(endpoint.problem or NO_AGENTS_NOTE,))
    if assets is None:
        return Reconciliation(
            notes=("El inventario no se puede leer: no se compara con los agentes.",)
        )
    try:
        registry = load_registry(store).agents
    except RegistryError:
        return Reconciliation(
            notes=(
                "El registro de agentes no se puede leer: no se sabe qué equipos están admitidos.",
            )
        )
    config, now = endpoint.config, loaded.now
    notes: list[str] = []
    evidence = load_host_evidence(store)
    if evidence.unreadable:
        notes.append(
            f"Hay {len(evidence.unreadable)} evidencias de agentes que no se han podido leer: "
            "puede faltar algún contacto."
        )
    if orphans := [i for i, r in registry.items() if not r.revoked and i not in config.hosts]:
        notes.append(
            f"Hay {len(orphans)} equipos admitidos que no figuran en connectors/endpoint.yaml: "
            "no se pueden comparar."
        )
    views = build_views(evidence.agent, config.hosts)
    limit = timedelta(hours=config.max_agent_silence_hours)
    admitted = {i: r for i, r in registry.items() if i in config.hosts and not r.revoked}
    revoked = {i for i, r in registry.items() if i in config.hosts and r.revoked}
    observed = ObservedHosts(
        source="agents/registry.yaml",
        observed_at=now,
        hosts=[
            ObservedHost(hostname=config.hosts[i].hostname, asset_id=i) for i in sorted(admitted)
        ],
    )
    report = reconcile(assets.assets, observed)
    if report.input_errors:
        notes.append("La lista de equipos admitidos no es coherente: no se compara.")
        return Reconciliation(notes=tuple(notes))

    def contact(asset_id: str) -> tuple[bool, str]:
        last = views.get(asset_id, AgentView()).latest_contact(admitted[asset_id].generation)
        if last is None:
            return False, "Sin contacto desde el alta."
        text = (
            f"Último contacto: {format_instant(last.collected_at)} (evidencia {last.evidence_id})."
        )
        return now - last.collected_at <= limit, text

    by_id = {a.id: a for a in assets.assets}
    rows: list[ReconcileRow] = []
    out_of_scope = {
        asset_id
        for asset_id, (scope, _) in _placements(loaded.docs.system.model).items()
        if scope == "excluded"
    }
    for discrepancy in report.discrepancies:
        asset_id = discrepancy.identity.removeprefix("asset:")
        if discrepancy.kind is DiscrepancyKind.NOT_OBSERVED and asset_id in out_of_scope:
            continue  # fuera del alcance: no se espera que tenga agente
        if discrepancy.kind is DiscrepancyKind.NOT_IN_INVENTORY:
            fresh, detail = contact(asset_id)
            hostname = config.hosts[asset_id].hostname
            kind = "not_inventoried" if fresh else "not_inventoried_silent"
            rows.append(
                ReconcileRow(kind, KIND_LABELS[kind], asset_id, hostname, None, False, detail,
                             _prefill(asset_id, hostname), None)
            )  # fmt: skip
        else:
            enrolment = config.hosts.get(asset_id)
            detail = "El alta está revocada." if asset_id in revoked else None
            rows.append(
                ReconcileRow("not_enrolled", KIND_LABELS["not_enrolled"], asset_id,
                             enrolment.hostname if enrolment else None, by_id[asset_id].hostname,
                             False, detail, None, _href(asset_id))
            )  # fmt: skip
    for asset_id in sorted(admitted):
        asset = by_id.get(asset_id)
        if asset is None or asset.type not in RECONCILED_TYPES:
            continue
        hostname = config.hosts[asset_id].hostname
        mismatch = asset.hostname is not None and normalize_hostname(
            asset.hostname
        ) != normalize_hostname(hostname)
        fresh, detail = contact(asset_id)
        if asset.deployment is Deployment.DEPLOYED and not fresh:
            kind = "admitted_silent"
        elif mismatch:
            kind = "hostname_mismatch"
        else:
            continue
        rows.append(
            ReconcileRow(kind, KIND_LABELS[kind], asset_id, hostname, asset.hostname, mismatch,
                         detail, None, _href(asset_id))
        )  # fmt: skip
    rows.sort(key=lambda r: (_KIND_ORDER[r.kind], r.asset_id))
    return Reconciliation(tuple(rows), tuple(notes))


# --- listado -------------------------------------------------------------------------------------


def _filters(query: Mapping[str, str]) -> Filters:
    def one(name: str, allowed: set[str]) -> str:
        value = query.get(name, "")
        return value if value in allowed else ""

    return Filters(
        tipo=one("tipo", {t.value for t in AssetType}),
        despliegue=one("despliegue", {d.value for d in Deployment}),
        alcance=one("alcance", {"included", "excluded", "undecided"}),
        estado=one("estado", {*STATUS_LABELS, NO_EVIDENCE}),
    )


def _filter_options() -> FilterOptions:
    return FilterOptions(
        tipo=tuple(Choice(t.value, ASSET_TYPE_LABELS[t]) for t in AssetType),
        despliegue=tuple(Choice(d.value, DEPLOYMENT_LABELS[d]) for d in Deployment),
        alcance=tuple(Choice(k, SCOPE_LABELS[k]) for k in ("included", "excluded", "undecided")),
        estado=(
            *(Choice(k, v) for k, v in STATUS_LABELS.items()),
            Choice(NO_EVIDENCE, "Sin evidencia"),
        ),
    )


def _matches(row: AssetRow, wanted: Filters) -> bool:
    status = row.evidence.status if row.evidence else NO_EVIDENCE
    return all(
        not chosen or chosen == actual
        for chosen, actual in (
            (wanted.tipo, row.type),
            (wanted.despliegue, row.deployment),
            (wanted.alcance, row.scope),
            (wanted.estado, status),
        )
    )


def _saved(request: Request) -> dict[str, str | None]:
    done = SAVED_TEXTS.get(request.query_params.get("hecho", ""))
    revoked = done is not None and request.query_params.get("revocada") == "1"
    return {"saved": done, "revoked_notice": REVOKED_NOTICE if revoked else None}


def _list_page(
    request: Request, *, status: int = 200, form_errors: list[str] | None = None
) -> Response:
    store, loaded = request.app.state.store, _load(request)
    rows = _rows(loaded)
    filters = _filters(request.query_params)
    reconciliation = build_reconciliation(store, loaded)
    page = InventoryPage(
        rows=tuple(r for r in rows if _matches(r, filters)),
        total=len(rows),
        filters=filters,
        filter_options=_filter_options(),
        file_errors=_errors(loaded.docs.document_errors),
        integrity_warnings=_errors(loaded.docs.integrity_errors),
        writes_blocked=loaded.writes_blocked,
        reconciliation=reconciliation,
        history_notes=unreadable_notes(loaded.scan),
        generated_at=format_instant(loaded.now),
    )
    return render(
        request,
        "views/inventario.html",
        status_code=status,
        **page_context(request, "inventario", "Inventario"),
        page=page,
        rows=page.rows,
        reconciliation=reconciliation,
        form_errors=form_errors or [],
        **_saved(request),
    )


def listado(request: Request) -> Response:
    return _list_page(request)


# --- formularios ---------------------------------------------------------------------------------


class Rejected(Exception):
    """Lo que no se pudo guardar: errores por campo y errores generales, sin valores."""

    def __init__(
        self, errors: dict[str, list[str]] | None = None, form_errors: list[str] | None = None
    ) -> None:
        super().__init__("rechazado")
        self.errors = errors or {}
        self.form_errors = form_errors or []


def _echo(form: Form) -> dict[str, str]:
    return {name: values[0] for name, values in form.items() if values}


def _text(value: Any) -> str:
    if isinstance(value, bool):
        return "sí" if value else "no"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def _approval_warning(store: UiStore) -> ApprovalWarning | None:
    status = scope_status(store)
    if status.state is ScopeState.APPROVED and status.approval is not None:
        return ApprovalWarning(
            status.approval.approved_by, format_instant(status.approval.approved_at)
        )
    return None


def _asset_types() -> tuple[Choice, ...]:
    return tuple(Choice(t.value, ASSET_TYPE_LABELS[t]) for t in AssetType)


def _form_fields(
    kind: AssetType | None,
) -> tuple[AssetType | None, str | None, tuple[FormField, ...]]:
    described = asset_form_fields(kind.value, with_optional=True) if kind else None
    if described is None:
        return None, None, ()
    fields = tuple(
        FormField(f"asset_{f['name']}", f["label"], f["kind"], f.get("required", True))
        for f in described["fields"]
    )
    return kind, described["type_label"], fields


def _kind(value: str) -> AssetType | None:
    try:
        return AssetType(value)
    except ValueError:
        return None


def _asset_form(
    store: UiStore, *, mode: str, asset_id: str, kind: AssetType | None, values: Mapping[str, str]
) -> AssetForm:
    _, label, fields = _form_fields(kind)
    return AssetForm(
        mode=mode,
        action=NEW_PATH if mode == "nuevo" else f"{_href(asset_id)}/editar",
        cancel_href=LIST_PATH if mode == "nuevo" else _href(asset_id),
        asset_id=asset_id,
        type=kind.value if kind else None,
        type_label=label,
        fields=fields,
        asset_types=_asset_types(),
        deployment=values.get("asset_deployment", Deployment.DEPLOYED.value),
        scope=values.get("scope", ""),
        justification=values.get("justification", ""),
        approval_warning=_approval_warning(store),
    )


def _blocked_context(loaded: Loaded) -> dict[str, Any]:
    return {
        "writes_blocked": loaded.writes_blocked,
        "file_errors": _errors(loaded.docs.document_errors),
        "integrity_warnings": _errors(loaded.docs.integrity_errors),
        "history_notes": unreadable_notes(loaded.scan),
        "generated_at": format_instant(loaded.now),
    }


def _new_page(
    request: Request,
    *,
    values: Mapping[str, str],
    rejected: Rejected | None = None,
    status: int = 200,
) -> Response:
    loaded = _load(request)
    kind = _kind(values.get("asset_type", ""))
    form = _asset_form(request.app.state.store, mode="nuevo", asset_id="", kind=kind, values=values)
    return render(
        request,
        "views/inventario_form.html",
        status_code=status,
        **page_context(request, "inventario", "Nuevo activo"),
        form=form,
        values=dict(values),
        errors=rejected.errors if rejected else {},
        form_errors=list(rejected.form_errors) if rejected else [],
        **_blocked_context(loaded),
    )


def nuevo(request: Request) -> Response:
    query = request.query_params
    values = {"asset_type": query.get("tipo", ""), "asset_id": query.get("id", "")}
    if hostname := query.get("hostname"):
        values["asset_hostname"] = hostname
    return _new_page(
        request, values={"scope": "included", **{k: v for k, v in values.items() if v}}
    )


def _asset_values(asset: Asset, placement: tuple[str, str | None]) -> dict[str, str]:
    kind_fields = _form_fields(asset.type)[2]
    shown = asset.model_dump(exclude_none=True)
    values = {
        f.name: _text(shown[f.name.removeprefix("asset_")])
        for f in kind_fields
        if f.name.removeprefix("asset_") in shown
    }
    scope, justification = placement
    return {
        **values,
        "asset_id": asset.id,
        "asset_deployment": asset.deployment.value,
        "scope": scope if scope in ("included", "excluded") else "",
        "justification": justification or "",
    }


def _removal(detail: AssetDetail, store: UiStore) -> Removal:
    asset_id = detail.row.id
    return Removal(
        asset_id=asset_id,
        action=f"{_href(asset_id)}/baja",
        cancel_href=_href(asset_id),
        blockers=detail.removal_blockers,
        references=(*detail.services, *detail.dependencies),
        approval_warning=_approval_warning(store),
    )


def _ficha_page(
    request: Request,
    asset_id: str,
    *,
    mode: str = "ver",
    values: Mapping[str, str] | None = None,
    rejected: Rejected | None = None,
    status: int = 200,
) -> Response:
    store, loaded = request.app.state.store, _load(request)
    asset = next((a for a in loaded.assets if a.id == asset_id), None)
    if asset is None:
        raise HTTPException(404)
    row = next(r for r in _rows(loaded) if r.id == asset_id)
    detail = _detail(loaded, row, asset)
    shown_mode = mode
    if rejected is None and not (permissions_of(request).edit and not loaded.writes_blocked):
        shown_mode = "ver"  # sin permiso o con el inventario inválido no se ofrece escribir
    placement = _placements(loaded.docs.system.model).get(asset_id, ("undecided", None))
    state = {**_asset_values(asset, placement), **(values or {})}
    form = (
        _asset_form(store, mode="editar", asset_id=asset_id, kind=asset.type, values=state)
        if shown_mode == "editar"
        else None
    )
    return render(
        request,
        "views/inventario_activo.html",
        status_code=status,
        **page_context(request, "inventario", asset_id),
        detail=detail,
        form=form,
        removal=_removal(detail, store) if shown_mode == "baja" else None,
        values=state,
        errors=rejected.errors if rejected else {},
        form_errors=list(rejected.form_errors) if rejected else [],
        **_saved(request),
        **_blocked_context(loaded),
    )


def ficha(request: Request) -> Response:
    asked = request.query_params
    mode = "editar" if asked.get("editar") == "1" else "baja" if asked.get("baja") == "1" else "ver"
    return _ficha_page(request, request.path_params["activo"], mode=mode)


# --- escrituras (K1, K2) -------------------------------------------------------------------------


def _guard(store: UiStore) -> tuple[ScopeDocuments, ApprovalWarning | None]:
    """Con el inventario válido, lo leído y la aprobación vigente (si la hay); si no, rechaza."""
    docs = load_documents(store)
    if docs.assets.model is None or docs.system.model is None:
        raise Rejected(
            form_errors=[BLOCKED_NOTICE, *_errors([*docs.assets.errors, *docs.system.errors])]
        )
    return docs, _approval_warning(store)


def _accepted(form: Form, warning: ApprovalWarning | None) -> dict[str, list[str]]:
    if warning is None or first(form, "acepto_revocacion"):
        return {}
    return {"acepto_revocacion": [ACCEPTANCE_REQUIRED]}


def _scope_choice(form: Form, *, required: bool) -> tuple[ScopeChoice | None, dict[str, list[str]]]:
    chosen = first(form, "scope")
    if chosen not in ("included", "excluded"):
        return None, {
            "scope": ["Elige si el activo entra en el alcance."]
        } if required or chosen else {}
    if chosen == "included":
        return ScopeChoice(True), {}
    if not (why := first(form, "justification")):
        return None, {"justification": ["Explica por qué se excluye."]}
    return ScopeChoice(False, why), {}


def _asset_changes(
    form: Form, kind: AssetType, *, blank_clears: bool
) -> tuple[dict, dict[str, list[str]]]:
    try:
        return asset_form_values(form, kind=kind, blank_clears=blank_clears), {}
    except StepRejected as exc:
        return {}, dict(exc.errors)


def _routable(asset_id: str) -> dict[str, list[str]]:
    if "/" in asset_id or asset_id in (".", ".."):
        return {"asset_id": ["El identificador no puede llevar «/» ni ser «.» o «..»."]}
    return {}


def _api(operation: Callable[[], bool]) -> bool:
    try:
        return operation()
    except ScopeEditRejected as exc:
        raise Rejected(form_errors=list(_errors(exc.errors))) from None


class _Overlay:
    """Lectura del workspace con las escrituras en memoria: ensayo que no toca nada."""

    def __init__(self, inner: UiStore) -> None:
        self._inner = inner
        self._pending: dict[str, str] = {}

    def read_text(self, key: str) -> str:
        return self._pending[key] if key in self._pending else self._inner.read_text(key)

    def read_bytes(self, key: str) -> bytes:
        return self.read_text(key).encode()

    def write_text(self, key: str, text: str) -> None:
        self._pending[key] = text

    def write_bytes(self, key: str, data: bytes) -> None:
        self._pending[key] = data.decode()

    def create_bytes(self, key: str, data: bytes) -> None:
        self.write_bytes(key, data)

    def exists(self, key: str) -> bool:
        return key in self._pending or self._inner.exists(key)

    def list(self, prefix: str = "") -> list[str]:
        return sorted(
            {*self._inner.list(prefix), *(k for k in self._pending if k.startswith(prefix))}
        )


def _api_errors(store: UiStore, operation: Callable[[Any], bool]) -> list[str]:
    """Lo que rechazaría la API con estos datos, ensayado sin escribir."""
    try:
        operation(_Overlay(store))
    except ScopeEditRejected as exc:
        return list(_errors(exc.errors))
    return []


def _create(store: UiStore, now: datetime, form: Form) -> bool:
    _, warning = _guard(store)
    errors = _accepted(form, warning)
    kind = _kind(first(form, "asset_type"))
    if kind is None:
        raise Rejected({**errors, "asset_type": ["Elige un tipo de activo."]})
    changes, field_errors = _asset_changes(form, kind, blank_clears=False)
    choice, scope_errors = _scope_choice(form, required=True)
    errors = {**errors, **field_errors, **scope_errors, **_routable(first(form, "asset_id"))}
    if errors:
        ready = not field_errors and "id" in changes
        raise Rejected(
            errors,
            _api_errors(
                store, lambda s: save_asset(s, changes["id"], changes, choice, now, create=True)
            )
            if ready
            else [],
        )
    return _api(lambda: save_asset(store, changes["id"], changes, choice, now, create=True))


def _edit(store: UiStore, now: datetime, asset_id: str, form: Form) -> bool:
    docs, warning = _guard(store)
    assert docs.assets.model is not None
    existing = next((a for a in docs.assets.model.assets if a.id == asset_id), None)
    if existing is None:
        raise HTTPException(404)
    kind_fields = [f.name for f in _form_fields(existing.type)[2]]
    posted = {**form, "asset_id": [asset_id]}
    changes, field_errors = _asset_changes(posted, existing.type, blank_clears=True)
    for name in kind_fields:  # lo que el formulario no trae se conserva
        if name not in form:
            changes.pop(name.removeprefix("asset_"), None)
    if "asset_deployment" not in form:
        changes.pop("deployment", None)
    choice, scope_errors = _scope_choice(form, required=False)
    errors = {**_accepted(form, warning), **field_errors, **scope_errors}
    if errors:
        raise Rejected(
            errors,
            _api_errors(
                store, lambda s: save_asset(s, asset_id, changes, choice, now, create=False)
            )
            if not field_errors
            else [],
        )
    return _api(lambda: save_asset(store, asset_id, changes, choice, now, create=False))


def _remove(store: UiStore, now: datetime, asset_id: str, form: Form) -> bool:
    _, warning = _guard(store)
    errors = _accepted(form, warning)
    if first(form, "confirmar") != asset_id:
        errors["confirmar"] = ["Escribe el identificador del activo para confirmar la baja."]
    if errors:
        raise Rejected(errors)
    return _api(lambda: remove_asset(store, asset_id, now))


ACTIONS: dict[str, Callable[[UiStore, datetime, str, Form], bool]] = {
    "editar": _edit,
    "baja": _remove,
}


def _audit(
    request: Request, failed: bool, action: str, asset_id: str, keys: list[str], at: datetime
) -> None:
    audit_write(
        identity=identity_of(request).email,
        step=STEP,
        action=action,
        keys=keys,
        at=at,
        asset=asset_id,
        failed=failed,
    )


async def _written(
    request: Request,
    store: UiStore,
    now: datetime,
    action: str,
    asset_id: str,
    work: Callable[[], bool],
) -> bool:
    """Ejecuta la escritura; si falla tras haber escrito algo, lo deja auditado y relanza."""
    try:
        revoked = await run_in_threadpool(work)
    except Rejected:
        raise
    except Exception:
        if store.written:
            _audit(request, True, action, asset_id, store.written, now)
        raise
    _audit(request, False, action, asset_id, store.written, now)
    return revoked


async def _read(request: Request) -> Form | Response:
    try:
        return await read_form(request)
    except FormTooLarge:
        return error_page(request, 413, "El formulario es demasiado grande.")
    except FormInvalid:
        return error_page(request, 400, "No se ha entendido el formulario.")


def _after(done: str, revoked: bool) -> str:
    return f"hecho={done}" + ("&revocada=1" if revoked else "")


async def crear(request: Request) -> Response:
    form = await _read(request)
    if isinstance(form, Response):
        return form
    store, now = request.app.state.store.recording(), request.app.state.clock()
    asset_id = first(form, "asset_id")
    try:
        revoked = await _written(
            request, store, now, "nuevo", asset_id, lambda: _create(store, now, form)
        )
    except Rejected as rejected:
        return _new_page(request, values=_echo(form), rejected=rejected, status=422)
    return RedirectResponse(f"{_href(asset_id)}?{_after('nuevo', revoked)}", status_code=303)


async def escribir(request: Request) -> Response:
    name = request.path_params["accion"]
    if name not in ACTIONS:
        raise HTTPException(404)
    asset_id = request.path_params["activo"]
    form = await _read(request)
    if isinstance(form, Response):
        return form
    store, now = request.app.state.store.recording(), request.app.state.clock()
    work = ACTIONS[name]
    try:
        revoked = await _written(
            request, store, now, name, asset_id, lambda: work(store, now, asset_id, form)
        )
    except Rejected as rejected:
        if not any(a.id == asset_id for a in _load(request).assets):
            # sin inventario legible no hay ficha que repintar: se explica en el listado
            return _list_page(request, status=422, form_errors=rejected.form_errors)
        return _ficha_page(
            request, asset_id, mode=name, values=_echo(form), rejected=rejected, status=422
        )
    if name == "baja":
        return RedirectResponse(f"{LIST_PATH}?{_after('baja', revoked)}", status_code=303)
    done = "editar" if store.written else "sin-cambios"
    return RedirectResponse(f"{_href(asset_id)}?{_after(done, revoked)}", status_code=303)


INVENTORY_ROUTES: list[BaseRoute] = [
    Route(LIST_PATH, listado, methods=["GET"]),
    Route(NEW_PATH, nuevo, methods=["GET"]),
    Route(NEW_PATH, crear, methods=["POST"]),
    Route(f"{ASSET_PATH}/{{activo}}", ficha, methods=["GET"]),
    Route(f"{ASSET_PATH}/{{activo}}/{{accion}}", escribir, methods=["POST"]),
]
