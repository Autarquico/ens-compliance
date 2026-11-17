"""Conectores: configuración sin secretos y estado de la última recogida de cada uno.

Las escrituras usan `save_connector_config` (el mismo modelo y la misma política de secretos que el
cargador). Los secretos se buscan en el formulario en bruto antes de convertir nada y el campo
marcado ni se repite ni se escribe. La plantilla recibe solo los DTO de este módulo y de
`conectores_recogida`: nunca el YAML, `data.error`, `Section.reason`, `CheckResult.reason` ni el
texto de una excepción.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from importlib.metadata import entry_points
from typing import Literal
from urllib.parse import quote

from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import BaseRoute, Route

from ens_evidence.checks.store import RunScan, scan_runs
from ens_evidence.connectors.config import connector_config_key, credential_fields
from ens_evidence.connectors.edit import (
    SECRET_IN_FIELD,
    ConnectorEditRejected,
    ExistingConfig,
    inspect,
    save_connector_config,
)
from ens_evidence.connectors.registry import (
    ENTRY_POINT_GROUP,
    ConnectorLoadError,
    ConnectorRegistry,
)
from ens_evidence.models.contracts import EvidenceManifest
from ens_evidence.models.enums import CollectionStatus
from ens_evidence.scheduling.collect_requests import (
    REQUESTS_PREFIX,
    RejectReason,
    RequestRejected,
    RequestStatus,
    request_collect,
)
from ens_evidence.scope.inventory import load_assets
from ens_evidence.scope.models import Asset
from ens_evidence.ui.audit import audit_write
from ens_evidence.ui.forms import Form, FormInvalid, FormTooLarge, first, read_form
from ens_evidence.ui.pages import (
    error_page,
    identity_of,
    page_context,
    permissions_of,
    render,
    status_badge,
)
from ens_evidence.ui.store import UiStore
from ens_evidence.ui.views import format_instant
from ens_evidence.ui.views.check_history import (
    ConnectorResults,
    Contribution,
    EvidenceSummary,
    detail_results,
    latest_results,
    summary,
    unreadable_notes,
)
from ens_evidence.ui.views.conectores_catalogo import (
    CATALOGUE,
    EDITABLE,
    Choice,
    ConnectorSpec,
    CredentialSpec,
    FieldSpec,
    Kind,
    catalogue_for,
    document_to_values,
    form_fields_to_document,
)
from ens_evidence.ui.views.conectores_peticion import RequestView, index_requests, request_view
from ens_evidence.ui.views.conectores_recogida import (
    CollectionIndex,
    LastCollection,
    RepositoryState,
    index_collections,
    last_collection,
    repository_states,
)
from ens_evidence.ui.views.inventory import BLOCKED_NOTICE
from ens_evidence.ui.wizard.scope import ASSET_TYPE_LABELS
from ens_evidence.workspace import WorkspaceError

STEP = "conectores"
ACTION = "configurar"
COLLECT_ACTION = "recoger"
LIST_PATH = "/conectores"
ASSET_PATH = "/inventario/activos"
NO_CHANGES = "sin-cambios"
CONFIRM_FIELD = "confirmar_sobrescritura"
ASSET_MISSING = "El activo no está en el inventario"
INVENTORY_UNREADABLE = (
    "El inventario no se puede leer: no se comprueba el activo de cada conector ni se puede editar."
)
SAVED_TEXT = "No había nada que cambiar: no se ha guardado nada."
CONFIG_LABELS = {
    "sin-configurar": "Sin configurar",
    "configurado": "Configurado",
    "invalida": "Configuración no válida",
    "ilegible": "Configuración ilegible",
}

ConfigStatus = Literal["sin-configurar", "configurado", "invalida", "ilegible"]
LastOutcome = Literal["failed", "partial", "ok", "unreadable"]


def config_invalid(name: str) -> str:
    return f"connectors/{name}.yaml no es válido para el cargador"


def config_unreadable(name: str) -> str:
    return f"connectors/{name}.yaml no se puede leer"


# --- DTO -----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CredentialInfo:
    kind: Literal["env", "env_prefix", "settings", "none"]
    names: tuple[str, ...]
    where: str


@dataclass(frozen=True)
class ConfigState:
    status: ConfigStatus
    label: str
    problem: str | None
    asset_id: str | None
    asset_href: str | None
    asset_note: str | None
    shown: tuple[tuple[str, str], ...]  # (etiqueta, valor) solo de campos del catálogo


@dataclass(frozen=True)
class ConnectorEvidence(EvidenceSummary):
    """Resultados de checks; `stale`: una recogida posterior a ellos; `last_outcome`: cómo acabó."""

    stale: bool = False
    last_outcome: LastOutcome | None = None


@dataclass(frozen=True)
class ConnectorBlock:
    name: str
    label: str
    href: str
    editable: bool
    can_configure: bool
    config: ConfigState
    credential: CredentialInfo | None  # None: conector sin ficha
    doc_href: str | None
    kit_href: str | None
    status_href: str | None
    last: LastCollection | None
    evidence: ConnectorEvidence | None
    results: tuple[ConnectorResults, ...]
    history_notes: tuple[str, ...]
    repositories: tuple[RepositoryState, ...] = ()  # solo `backups`
    request: RequestView | None = None  # la última petición de recogida del conector
    can_collect: bool = False
    collect_action: str = ""


@dataclass(frozen=True)
class ConectoresPage:
    blocks: tuple[ConnectorBlock, ...]
    manifest_notes: tuple[str, ...]
    inventory_notes: tuple[str, ...]
    generated_at: str
    registry_unreadable: bool = False  # el catálogo se muestra sin el registro de conectores


@dataclass(frozen=True)
class FormField:
    name: str
    label: str
    kind: Kind
    required: bool
    hint: str | None
    choices: tuple[Choice, ...]


@dataclass(frozen=True)
class ConnectorForm:
    name: str
    label: str
    action: str
    cancel_href: str
    fields: tuple[FormField, ...]
    asset_choices: tuple[Choice, ...]
    asset_type_label: str
    preserved: tuple[str, ...]
    needs_confirmation: bool
    writes_blocked: bool
    blocked_notice: str | None
    credential: CredentialInfo
    doc_href: str | None


# --- registro y recursos compartidos por petición ------------------------------------------------


def registered_connectors() -> tuple[dict[str, type[BaseModel]], bool]:
    """Nombre → modelo de los conectores registrados (entry points) y si el registro falló."""
    registry = ConnectorRegistry()
    try:
        registry.load_entry_points(entry_points(group=ENTRY_POINT_GROUP))
    except ConnectorLoadError:
        return {n: s.model for n, s in CATALOGUE.items() if s.model is not None}, True
    return {name: registry.get(name).config_model for name in registry.names()}, False


@dataclass(frozen=True)
class _Inventory:
    assets: tuple[Asset, ...]
    readable: bool

    def choices(self, spec: ConnectorSpec) -> tuple[Choice, ...]:
        return tuple(Choice(a.id, a.id) for a in self.assets if a.type is spec.asset_type)


def _inventory(store: UiStore) -> _Inventory:
    assets, errors = load_assets(store)
    return _Inventory(tuple(assets.assets) if assets else (), assets is not None and not errors)


def _credential(spec: CredentialSpec) -> CredentialInfo:
    return CredentialInfo(spec.kind, spec.names, spec.where)


def _asset_link(asset_id: str, inventory: _Inventory) -> tuple[str | None, str | None]:
    if not inventory.readable:
        return None, None
    if any(a.id == asset_id for a in inventory.assets):
        return f"{ASSET_PATH}/{quote(asset_id, safe='')}", None
    return None, ASSET_MISSING


# --- estado de la configuración ------------------------------------------------------------------


def _existing(store: UiStore, name: str, model: type[BaseModel] | None) -> ExistingConfig | None:
    """`None` si no se puede leer (o no hay modelo con el que validar)."""
    if model is None:
        return None
    try:
        return inspect(store, name, model)
    except WorkspaceError:
        return None


def _display(field: FieldSpec, value: str) -> str:
    labels = {c.value: c.label for c in field.choices}
    return labels.get(value, value).replace("\n", ", ")


def _problem(status: ConfigStatus, name: str) -> ConfigState:
    problems = {"invalida": config_invalid(name), "ilegible": config_unreadable(name)}
    return ConfigState(status, CONFIG_LABELS[status], problems.get(status), None, None, None, ())


def _config_state(
    name: str, spec: ConnectorSpec, existing: ExistingConfig | None, inventory: _Inventory
) -> ConfigState:
    if existing is None:
        return _problem("ilegible", name)
    if existing.state == "absent":
        return _problem("sin-configurar", name)
    if existing.state != "valid" or existing.raw is None:
        return _problem("invalida", name)
    values = document_to_values(spec, existing.raw)
    shown = tuple((f.label, _display(f, values[f.name])) for f in spec.fields if values.get(f.name))
    asset_id = values.get("asset_id") or None
    href, note = _asset_link(asset_id, inventory) if asset_id else (None, None)
    return ConfigState(
        "configurado", CONFIG_LABELS["configurado"], None, asset_id, href, note, shown
    )


def _repository_ids(existing: ExistingConfig | None) -> tuple[str, ...]:
    if existing is None or existing.state != "valid" or existing.raw is None:
        return ()
    return tuple(r["id"] for r in existing.raw["repositories"])


def _evidence(
    scan: RunScan,
    contributions: list[Contribution],
    manifest: EvidenceManifest | None,
    last: LastCollection | None,
) -> ConnectorEvidence | None:
    """Los resultados de checks; si una recogida posterior consta, no se dan por vigentes."""
    if not contributions:
        return None
    base = summary(scan, contributions)
    newest = max(run.finished_at for run, _, _ in contributions)
    if manifest is None or last is None or manifest.collected_at <= newest:
        return ConnectorEvidence(**vars(base))
    outcome: LastOutcome = (
        "unreadable"
        if last.unreadable
        else "partial"
        if last.partial
        else "ok"
        if last.status == CollectionStatus.SUCCESS
        else "failed"
    )
    _, label = status_badge(base.status, True)
    return ConnectorEvidence(
        **{
            **vars(base),
            "label": label,
            "favorable": False,
            "coverage": "parcial",
        },
        stale=True,
        last_outcome=outcome,
    )


# --- bloque de un conector -----------------------------------------------------------------------


def _block(
    store: UiStore,
    spec: ConnectorSpec,
    registered: Mapping[str, type[BaseModel]],
    index: CollectionIndex,
    requests: Mapping[str, RequestStatus],
    inventory: _Inventory,
    can_edit: bool,
) -> ConnectorBlock:
    name = spec.name
    existing = _existing(store, name, spec.model or registered.get(name))
    config = _config_state(name, spec, existing, inventory)
    manifest = index.latest.get(name)
    scan = scan_runs(store, name)
    # `backups` se proyecta aparte (D3): sus checks y su detalle están en /copias.
    asset_id = config.asset_id if name != "backups" else None
    contributions = latest_results(scan).get(asset_id, []) if asset_id else []
    last = last_collection(store, manifest, name) if manifest else None
    latest = requests.get(name)
    pending = request_view(latest) if latest else None
    return ConnectorBlock(
        name=name,
        label=spec.label,
        href=f"{LIST_PATH}/{quote(name, safe='')}",
        editable=spec.editable,
        can_configure=spec.editable and can_edit,
        config=config,
        credential=_credential(spec.credential) if spec.credential else None,
        doc_href=spec.doc_href,
        kit_href=spec.kit_href,
        status_href=spec.status_href,
        last=last,
        evidence=_evidence(scan, contributions, manifest, last),
        results=detail_results(contributions),
        history_notes=unreadable_notes(scan),
        repositories=(
            repository_states(store, manifest, _repository_ids(existing))
            if name == "backups" and manifest
            else ()
        ),
        request=pending,
        can_collect=can_edit and config.status == "configurado" and not (pending and pending.alive),
        collect_action=f"{LIST_PATH}/{quote(name, safe='')}/{COLLECT_ACTION}",
    )


def build_page(store: UiStore, can_edit: bool, now: datetime) -> ConectoresPage:
    registered, registry_failed = registered_connectors()
    index = index_collections(store)  # un solo `list_manifests` por petición
    requests = index_requests(store, now)  # y un solo `list` de peticiones
    inventory = _inventory(store)
    blocks = tuple(
        _block(store, spec, registered, index, requests, inventory, can_edit)
        for spec in catalogue_for(registered)
    )
    return ConectoresPage(
        blocks=blocks,
        manifest_notes=(
            (
                f"Hay {index.unreadable_manifests} evidencias que no se han podido leer: "
                "puede faltar alguna recogida.",
            )
            if index.unreadable_manifests
            else ()
        ),
        inventory_notes=() if inventory.readable else (INVENTORY_UNREADABLE,),
        registry_unreadable=registry_failed,
        generated_at=format_instant(now),
    )


# --- formulario ----------------------------------------------------------------------------------


def _form_field(field: FieldSpec, asset_choices: tuple[Choice, ...]) -> FormField:
    choices = asset_choices if field.kind == "asset" else field.choices
    return FormField(field.name, field.label, field.kind, field.required, field.hint, choices)


def _blocked(inventory: _Inventory) -> str | None:
    return None if inventory.readable else BLOCKED_NOTICE


def build_form(
    spec: ConnectorSpec, existing: ExistingConfig | None, inventory: _Inventory
) -> ConnectorForm:
    assert spec.model is not None and spec.credential is not None and spec.asset_type is not None
    choices = inventory.choices(spec)
    notice = _blocked(inventory) or (config_unreadable(spec.name) if existing is None else None)
    return ConnectorForm(
        name=spec.name,
        label=spec.label,
        action=f"{LIST_PATH}/{quote(spec.name, safe='')}",
        cancel_href=LIST_PATH,
        fields=tuple(_form_field(f, choices) for f in spec.fields),
        asset_choices=choices,
        asset_type_label=ASSET_TYPE_LABELS[spec.asset_type],
        preserved=tuple(sorted(spec.preserved)),
        needs_confirmation=existing is not None and existing.state in ("unparseable", "invalid"),
        writes_blocked=notice is not None,
        blocked_notice=notice,
        credential=_credential(spec.credential),
        doc_href=spec.doc_href,
    )


def _prefill(spec: ConnectorSpec, existing: ExistingConfig | None) -> dict[str, str]:
    """Solo de un fichero válido: ante uno inválido el formulario sale vacío (D6)."""
    if existing is None or existing.state != "valid" or existing.raw is None:
        return {}
    return {k: v for k, v in document_to_values(spec, existing.raw).items() if v}


def _echo(spec: ConnectorSpec, form: Form, flagged: set[str]) -> dict[str, str]:
    """Lo escrito en los campos del catálogo, sin los marcados como secretos."""
    return {
        f.name: form[f.name][0] if form.get(f.name) else ""
        for f in spec.fields
        if f.name not in flagged
    }


def _page_of(
    request: Request,
    spec: ConnectorSpec,
    *,
    registered: Mapping[str, type[BaseModel]] | None = None,
    values: Mapping[str, str] | None = None,
    errors: Mapping[str, list[str]] | None = None,
    form_errors: list[str] | None = None,
    status: int = 200,
) -> Response:
    store: UiStore = request.app.state.store
    inventory = _inventory(store)
    can_edit = permissions_of(request).edit
    now = request.app.state.clock()
    block = _block(
        store,
        spec,
        registered or {},
        index_collections(store),
        index_requests(store, now),
        inventory,
        can_edit,
    )
    form = None
    prefill: dict[str, str] = {}
    if spec.editable:
        existing = _existing(store, spec.name, spec.model)
        form = build_form(spec, existing, inventory)
        prefill = _prefill(spec, existing)
    return render(
        request,
        "views/conectores_form.html",
        status_code=status,
        **page_context(request, "conectores", spec.label),
        form=form,
        block=block,
        values=dict(prefill if values is None else values),
        errors=dict(errors or {}),
        form_errors=list(form_errors or []),
    )


# --- lectura -------------------------------------------------------------------------------------


def _saved(request: Request) -> tuple[str | None, str | None]:
    done = request.query_params.get("hecho", "")
    if done == NO_CHANGES:
        return done, SAVED_TEXT
    if done in EDITABLE:
        return done, f"Configuración de {CATALOGUE[done].label} guardada."
    return None, None


def _requested(request: Request) -> str | None:
    name = request.query_params.get("pedida", "")
    if name not in CATALOGUE:
        return None
    return (
        f"Petición de recogida de {CATALOGUE[name].label} creada. "
        "Se recogerá en cuanto el servicio esté libre; normalmente en menos de un minuto."
    )


def _listing(
    request: Request, *, form_errors: list[str] | None = None, status: int = 200
) -> Response:
    page = build_page(
        request.app.state.store, permissions_of(request).edit, request.app.state.clock()
    )
    saved, saved_text = _saved(request)
    return render(
        request,
        "views/conectores.html",
        status_code=status,
        **page_context(request, "conectores", "Conectores"),
        page=page,
        saved=saved,
        saved_text=saved_text,
        requested_text=_requested(request),
        form_errors=list(form_errors or []),
    )


def listado(request: Request) -> Response:
    return _listing(request)


def ficha(request: Request) -> Response:
    registered, _ = registered_connectors()
    name = request.path_params["conector"]
    spec = next((s for s in catalogue_for(registered) if s.name == name), None)
    if spec is None:
        raise HTTPException(404)
    return _page_of(request, spec, registered=registered)


# --- escritura -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Prepared:
    document: dict
    errors: dict[str, list[str]]
    flagged: set[str]


def _flagged(spec: ConnectorSpec, form: Form) -> set[str]:
    """Campos del catálogo que la política de credenciales rechaza, en bruto y antes de nada."""
    return set(credential_fields({f.name: list(form.get(f.name, [])) for f in spec.fields}))


def _prepare(spec: ConnectorSpec, form: Form, choices: tuple[Choice, ...]) -> _Prepared:
    """D1: primero los secretos en bruto; solo con los campos del catálogo hacia el escritor."""
    flagged = _flagged(spec, form)
    clean = {f.name: "" if f.name in flagged else first(form, f.name) for f in spec.fields}
    document, errors = form_fields_to_document(spec, clean)
    for name in flagged:
        errors[name] = [SECRET_IN_FIELD]
        document.pop(name, None)
    chosen = document.get("asset_id")
    if chosen is not None and chosen not in {c.value for c in choices}:
        assert spec.asset_type is not None
        label = ASSET_TYPE_LABELS[spec.asset_type]
        errors["asset_id"] = [f"Elige un activo de tipo «{label}» del inventario."]
    return _Prepared(document, errors, flagged)


async def _read(request: Request) -> Form | Response:
    try:
        return await read_form(request)
    except FormTooLarge:
        return error_page(request, 413, "El formulario es demasiado grande.")
    except FormInvalid:
        return error_page(request, 400, "No se ha entendido el formulario.")


def _audit(
    request: Request,
    name: str,
    failed: bool,
    keys: list[str],
    at: datetime,
    *,
    action: str = ACTION,
    fallback: str | None = None,
) -> None:
    audit_write(
        identity=identity_of(request).email,
        step=STEP,
        action=action,
        keys=(keys or [fallback or connector_config_key(name)]) if failed else keys,
        at=at,
        asset=name,
        failed=failed,
    )


async def _written(
    request: Request, store: UiStore, now: datetime, name: str, work: Callable[[], bool]
) -> bool:
    """Ejecuta y audita la escritura; un fallo de persistencia se audita aunque no se escribiera."""
    try:
        changed = await run_in_threadpool(work)
    except ConnectorEditRejected:
        raise
    except Exception:
        _audit(request, name, True, store.written, now)
        raise
    _audit(request, name, False, store.written, now)
    return changed


async def escribir(request: Request) -> Response:
    name = request.path_params["conector"]
    spec = CATALOGUE.get(name)
    if spec is None or not spec.editable or spec.model is None:
        raise HTTPException(404)
    form = await _read(request)
    if isinstance(form, Response):
        return form
    store, now = request.app.state.store.recording(), request.app.state.clock()
    flagged = _flagged(spec, form)  # ningún 422 repite un valor marcado
    inventory = _inventory(store)
    if not inventory.readable:
        return _page_of(
            request,
            spec,
            values=_echo(spec, form, flagged),
            form_errors=[BLOCKED_NOTICE],
            status=422,
        )
    prepared = _prepare(spec, form, inventory.choices(spec))
    if prepared.errors:
        return _page_of(
            request,
            spec,
            values=_echo(spec, form, flagged),
            errors=prepared.errors,
            status=422,
        )
    replace = first(form, CONFIRM_FIELD) == "1"
    model = spec.model
    try:
        changed = await _written(
            request,
            store,
            now,
            name,
            lambda: save_connector_config(
                store, name, model, prepared.document, replace_invalid=replace
            ),
        )
    except ConnectorEditRejected as rejected:
        return _page_of(
            request,
            spec,
            values=_echo(spec, form, flagged | _secret_fields(rejected)),
            errors=rejected.errors,
            form_errors=rejected.general,
            status=422,
        )
    return RedirectResponse(f"{LIST_PATH}?hecho={name if changed else NO_CHANGES}", status_code=303)


REJECTED_TEXTS = {
    RejectReason.NOT_CONFIGURED: "El conector {label} no está configurado.",
    RejectReason.BUSY: "Ya hay una petición de recogida de {label} sin terminar.",
    RejectReason.CONCURRENT: "Otra petición de {label} se creó a la vez; revisa y reintenta.",
}


def _rejected_messages(rejected: RequestRejected, label: str) -> list[str]:
    template = REJECTED_TEXTS.get(rejected.reason)
    if template:
        return [template.format(label=label)]
    return [e[:1].upper() + e[1:] for e in rejected.errors]


def _audit_collect(request: Request, name: str, failed: bool, keys: list[str], at: datetime):
    _audit(
        request,
        name,
        failed,
        keys,
        at,
        action=COLLECT_ACTION,
        fallback=f"{REQUESTS_PREFIX}/{name}/",
    )


async def recoger(request: Request) -> Response:
    """Deja una petición de recogida: no recoge nada; el fichero lo ejecuta el servicio `app`."""
    name = request.path_params["conector"]
    if name not in CATALOGUE:
        raise HTTPException(404)
    store, now = request.app.state.store.recording(), request.app.state.clock()
    try:
        await run_in_threadpool(
            request_collect, store, name, email=identity_of(request).email, now=now
        )
    except RequestRejected as rejected:
        messages = _rejected_messages(rejected, CATALOGUE[name].label)
        return _listing(request, form_errors=messages, status=422)
    except Exception:
        _audit_collect(request, name, True, store.written, now)
        raise
    _audit_collect(request, name, False, store.written, now)
    return RedirectResponse(f"{LIST_PATH}?pedida={quote(name, safe='')}", status_code=303)


def _secret_fields(rejected: ConnectorEditRejected) -> set[str]:
    """Campos que el escritor rechazó por secreto: tampoco se repiten."""
    return {f for f, messages in rejected.errors.items() if SECRET_IN_FIELD in messages}


CONECTORES_ROUTES: list[BaseRoute] = [
    Route(LIST_PATH, listado, methods=["GET"]),
    Route(f"{LIST_PATH}/{{conector}}", ficha, methods=["GET"]),
    Route(f"{LIST_PATH}/{{conector}}", escribir, methods=["POST"]),
    Route(f"{LIST_PATH}/{{conector}}/{COLLECT_ACTION}", recoger, methods=["POST"]),
]
