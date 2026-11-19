"""Documentos organizativos: estado de cada uno, fuente, aprobación y qué falta para generarlos.

Solo lee. La lógica es la del asistente (`wizard/docs_shared.py`) y la de las APIs de producto
(`orgdocs`); aquí solo se presenta. La plantilla recibe únicamente los DTO de este módulo.
"""

import secrets
import threading
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import cache
from typing import Any

import yaml
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import BaseRoute, Route

from ens_evidence.approvals import safe_load_unique
from ens_evidence.orgdocs import (
    ANSWERS_KEY,
    AnswersInvalid,
    BuildReport,
    DocumentState,
    DocumentStatus,
    all_statuses,
    build_documents,
    document_names,
    load_templates,
    validate_answers,
)
from ens_evidence.orgdocs.answers import AnswersReport, read_answers
from ens_evidence.orgdocs.approval import (
    DocumentApproval,
    DocumentApprovalRejected,
    UnknownDocument,
    approve_document,
    document_status,
    required_role,
    scan_approvals,
)
from ens_evidence.orgdocs.build import BuildStatus
from ens_evidence.orgdocs.keys import document_key, meta_key
from ens_evidence.orgdocs.meta import DocumentMeta
from ens_evidence.scope import ScopeNotApproved, ScopeState, scope_status
from ens_evidence.scope.secrets import find_secrets
from ens_evidence.ui.audit import audit_write
from ens_evidence.ui.forms import FormInvalid, FormTooLarge, first, read_form
from ens_evidence.ui.pages import (
    error_page,
    identity_of,
    page_context,
    permissions_of,
    render,
)
from ens_evidence.ui.roles import Permissions, role_options
from ens_evidence.ui.store import UiStore
from ens_evidence.ui.views import format_instant
from ens_evidence.ui.wizard.base import StepRejected, declared_name
from ens_evidence.ui.wizard.docs_shared import (
    ROLE_LABELS,
    SCOPE_NOT_APPROVED,
    document_rows,
    issue_groups,
    plain,
    signatory,
    titles,
)
from ens_evidence.workspace import NotFound, NotText, WorkspaceError

LIST_PATH = "/documentos"
WIZARD_PATH = "/asistente"
SCOPE_STEP = "aprobacion-alcance"
START_BLOCK = "documentos-organizacion"
RESPONSABLES_BLOCK = "documentos-responsables"
MAX_DOCUMENT_BYTES = 1024 * 1024

NOT_STARTED = "Todavía no has respondido el cuestionario. Empieza por «Tu organización»."
INVALID_ANSWERS = "Aún no se pueden generar los documentos: faltan respuestas o no son válidas."
NO_AUTHORITY = (
    "Antes de aprobar este documento di quién aprueba los procedimientos, en el bloque "
    "«Responsables y designación»."
)
UNKNOWN_DOCUMENT = "No reconocemos ese documento."
SAVED_VALUES = frozenset({"aprobado"})
STEP = "documentos"


# --- DTO -----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceInfo:
    template_id: str
    template_version: int
    renderer_version: int
    document_sha256: str
    catalog_sha256: str
    catalog_consolidation_date: str
    articulado_sha256: str
    articulado_consolidation_date: str


@dataclass(frozen=True)
class ApprovalInfo:
    approved_by: str
    role: str
    role_label: str
    approved_at: str


@dataclass(frozen=True)
class ApproverInfo:
    role: str | None
    role_label: str | None
    signatory: str
    responsables_href: str


@dataclass(frozen=True)
class IssueGroup:
    slug: str | None
    label: str
    items: tuple[str, ...]
    href: str | None


@dataclass(frozen=True)
class DocumentRow:
    name: str
    title: str
    href: str
    state: str
    state_label: str
    tone: str
    reasons: tuple[str, ...]
    source: SourceInfo | None
    approval: ApprovalInfo | None
    """Solo con el documento aprobado y vigente: nunca se presenta como favorable otra cosa."""
    last_approval: ApprovalInfo | None
    """La última aprobación legible, sea cual sea el estado actual."""
    approver: ApproverInfo
    missing: tuple[IssueGroup, ...]
    can_approve: bool
    """El estado admite aprobar (generado o caducado); no dice si esta persona puede."""
    role_missing: bool
    role_options: tuple[dict, ...]
    error_key: str
    unreadable: int
    drift_notice: str
    """Por qué el documento no está al día; se muestra junto al formulario, antes de enviar."""
    role_notice: str
    """Por qué esta persona no puede aprobar (falta el rol o no se ha declarado quién aprueba)."""
    can_submit: bool
    """Se ofrece el formulario de aprobar: estado válido, rol declarado y asignado."""


@dataclass(frozen=True)
class OutcomeDocument:
    name: str
    sha256: str
    archived: bool = False
    """Solo en los actualizados: había una edición manual y su versión anterior se archivó."""


@dataclass(frozen=True)
class GenerateOutcome:
    created: tuple[OutcomeDocument, ...]
    updated: tuple[OutcomeDocument, ...]
    unchanged: tuple[OutcomeDocument, ...]

    @property
    def archived(self) -> tuple[OutcomeDocument, ...]:
        return tuple(d for d in self.updated if d.archived)


OUTCOME_TTL = timedelta(minutes=5)
OUTCOME_CAPACITY = 256


class OutcomeShelf:
    """Los resultados de generación pendientes de enseñar, cada uno tras su propio token.

    Vive en la memoria del servidor (no es un secreto ni sale en la URL: solo viaja el token
    opaco): el POST deja el resultado y el GET que trae ese token, de la misma identidad, lo
    retira. Caduca y está acotado."""

    def __init__(self, ttl: timedelta = OUTCOME_TTL, capacity: int = OUTCOME_CAPACITY) -> None:
        self._ttl, self._capacity = ttl, capacity
        self._entries: OrderedDict[str, tuple[str, datetime, GenerateOutcome]] = OrderedDict()
        self._lock = threading.Lock()

    def put(self, identity: str, outcome: GenerateOutcome, now: datetime) -> str:
        token = secrets.token_urlsafe(16)
        with self._lock:
            self._entries[token] = (identity, now + self._ttl, outcome)
            while len(self._entries) > self._capacity:
                self._entries.popitem(last=False)
        return token

    def take(self, identity: str, token: str, now: datetime) -> GenerateOutcome | None:
        with self._lock:
            entry = self._entries.get(token)
            if entry is None or entry[0] != identity:  # ajeno o desconocido: no se toca
                return None
            del self._entries[token]
        return entry[2] if now < entry[1] else None


@dataclass(frozen=True)
class GenerationBlockers:
    scope_ok: bool
    answers_started: bool
    reasons: tuple[str, ...]
    groups: tuple[IssueGroup, ...]

    @property
    def clear(self) -> bool:
        return not self.reasons


@dataclass(frozen=True)
class DocumentsPage:
    rows: tuple[DocumentRow, ...]
    scope_ok: bool
    answers_started: bool
    issue_groups: tuple[IssueGroup, ...]
    warnings: tuple[str, ...]
    can_generate: bool
    """Se ofrece «Generar»: sin impedimentos y con permiso de edición."""
    blockers: tuple[str, ...]
    outcome: GenerateOutcome | None
    generated_at: str
    scope_href: str
    """Paso del asistente que aprueba el alcance; vacío si ya está aprobado."""
    start_href: str
    """Primer bloque del cuestionario."""


@dataclass(frozen=True)
class ClauseRow:
    id: str
    origin: str
    section: str


@dataclass(frozen=True)
class MeasureRow:
    id: str
    covered: tuple[str, ...]
    missing: tuple[str, ...]


@dataclass(frozen=True)
class DocumentDetail:
    row: DocumentRow
    text: str
    truncated: bool
    clauses: tuple[ClauseRow, ...]
    measures: tuple[MeasureRow, ...]
    history: tuple[ApprovalInfo, ...]
    unreadable: tuple[str, ...]


# --- lectura -------------------------------------------------------------------------------------


@cache
def _own_questions(name: str) -> frozenset[str]:
    for loaded in load_templates():
        if loaded.id == name:
            return frozenset(q.id for q in loaded.template.questions)
    return frozenset()


def _report(store: UiStore) -> AnswersReport:
    """Las respuestas validadas; un fichero ilegible o de otro tipo es un problema con su causa."""
    try:
        raw = read_answers(store) if store.exists(ANSWERS_KEY) else None
    except ValueError as exc:
        return AnswersReport(problems=[str(exc)])
    return validate_answers(raw, load_templates())


def _read_meta(store: UiStore, name: str) -> DocumentMeta | None:
    try:
        return DocumentMeta.model_validate(safe_load_unique(store.read_text(meta_key(name))))
    except (NotFound, NotText, yaml.YAMLError, ValidationError):
        return None


def _meta_of(store: UiStore, status: DocumentStatus) -> DocumentMeta | None:
    """El meta de un documento generado; sin generar no hay nada que leer."""
    if status.state is DocumentState.NOT_GENERATED:
        return None
    return _read_meta(store, status.name)


def _read_text(store: UiStore, name: str) -> tuple[str, bool]:
    """El texto para mostrar (recortado a 1 MiB) y si se recortó; vacío si no se puede leer.

    Solo se recorta la presentación: ningún estado se deriva de este texto."""
    try:
        raw = store.read_bytes(document_key(name), MAX_DOCUMENT_BYTES + 1)
    except WorkspaceError:
        return "", False
    truncated = len(raw) > MAX_DOCUMENT_BYTES
    return raw[:MAX_DOCUMENT_BYTES].decode("utf-8", errors="replace"), truncated


# --- impedimentos para generar --------------------------------------------------------------------


def _groups(raw: list[dict]) -> tuple[IssueGroup, ...]:
    return tuple(
        IssueGroup(
            g["slug"],
            g["label"],
            tuple(g["items"]),
            f"{WIZARD_PATH}/{g['slug']}" if g["slug"] else None,
        )
        for g in raw
    )


def generation_blockers(store: UiStore, report: AnswersReport) -> GenerationBlockers:
    """Todo lo que impide generar, de una vez: alcance, cuestionario sin empezar y respuestas.

    `build_documents` sigue siendo la validación final antes de escribir."""
    scope_ok = scope_status(store).state is ScopeState.APPROVED
    started = store.exists(ANSWERS_KEY)
    reasons = []
    if not scope_ok:
        reasons.append(SCOPE_NOT_APPROVED)
    if not started:
        reasons.append(NOT_STARTED)
    elif not report.valid:
        reasons.append(INVALID_ANSWERS)
    return GenerationBlockers(scope_ok, started, tuple(reasons), _groups(issue_groups(report)))


def generate_outcome(report: BuildReport) -> GenerateOutcome:
    def documents(kind: BuildStatus) -> tuple[OutcomeDocument, ...]:
        return tuple(
            OutcomeDocument(d.name, d.document_sha256, d.edited_by_hand)
            for d in report.documents
            if d.status is kind
        )

    return GenerateOutcome(
        created=documents(BuildStatus.CREATED),
        updated=documents(BuildStatus.UPDATED),
        unchanged=documents(BuildStatus.UNCHANGED),
    )


# --- filas ----------------------------------------------------------------------------------------


def _approval(approval: DocumentApproval | None) -> ApprovalInfo | None:
    if approval is None:
        return None
    return ApprovalInfo(
        approval.approved_by,
        approval.role.value,
        ROLE_LABELS[approval.role],
        format_instant(approval.approved_at),
    )


def _source(meta: DocumentMeta | None) -> SourceInfo | None:
    if meta is None:
        return None
    return SourceInfo(
        meta.template.id,
        meta.template.version,
        meta.renderer_version,
        meta.document_sha256,
        meta.catalog_sha256,
        meta.catalog_consolidation_date.isoformat(),
        meta.articulado_sha256,
        meta.articulado_consolidation_date.isoformat(),
    )


def _drift_notice(reasons: tuple[str, ...]) -> str:
    if not reasons:
        return ""
    return (
        f"Este documento no está al día: {'; '.join(reasons)}. "
        "Genera los documentos de nuevo y revísalos antes de aprobar."
    )


def _row(
    status: DocumentStatus,
    base: dict,
    report: AnswersReport,
    permissions: Permissions,
    meta: DocumentMeta | None,
    last_approval: DocumentApproval | None,
) -> DocumentRow:
    name = status.name
    role = required_role(name, report.values)
    role_missing = role is not None and permissions.lacks(role)
    can_approve = status.state in (DocumentState.GENERATED, DocumentState.OBSOLETE)
    reasons = tuple(base["reasons"])
    if role is None:
        role_notice = NO_AUTHORITY
    elif role_missing:
        role_notice = f"Este documento lo aprueba {ROLE_LABELS[role]}: tu cuenta no tiene ese rol."
    else:
        role_notice = ""
    return DocumentRow(
        name=name,
        title=base["title"],
        href=f"{LIST_PATH}/{name}",
        state=base["state"],
        state_label=base["state_label"],
        tone=base["tone"],
        reasons=reasons,
        source=_source(meta),
        approval=_approval(status.approval) if status.state is DocumentState.APPROVED else None,
        last_approval=_approval(last_approval),
        approver=ApproverInfo(
            role.value if role else None,
            ROLE_LABELS[role] if role else None,
            signatory(role, report.values),
            f"{WIZARD_PATH}/{RESPONSABLES_BLOCK}",
        ),
        missing=_groups(issue_groups(report, only=set(_own_questions(name)))),
        can_approve=can_approve,
        role_missing=role_missing,
        role_options=tuple(role_options([role])) if role else (),
        error_key=f"approved_by~{name}",
        unreadable=len(status.unreadable),
        drift_notice=_drift_notice(reasons) if can_approve else "",
        role_notice=role_notice if can_approve and permissions.edit else "",
        can_submit=can_approve and role is not None and not role_missing,
    )


def _last_approval(store: UiStore, status: DocumentStatus) -> DocumentApproval | None:
    """La última aprobación legible; sin generar el estado no la lee, pero sigue siendo historia."""
    if status.state is not DocumentState.NOT_GENERATED:
        return status.approval
    history, _ = scan_approvals(store, status.name)
    return history[-1] if history else None


def build_page(
    store: UiStore,
    now: datetime,
    permissions: Permissions,
    outcome: GenerateOutcome | None = None,
) -> DocumentsPage:
    statuses = all_statuses(store)  # una sola vez por petición
    base_rows = document_rows(store, statuses)
    report = _report(store)
    blockers = generation_blockers(store, report)
    rows = tuple(
        _row(
            status,
            base,
            report,
            permissions,
            _meta_of(store, status),
            _last_approval(store, status),
        )
        for status, base in zip(statuses, base_rows, strict=True)
    )
    return DocumentsPage(
        rows=rows,
        scope_ok=blockers.scope_ok,
        answers_started=blockers.answers_started,
        issue_groups=blockers.groups,
        warnings=tuple(plain(w) for w in report.warnings),
        can_generate=blockers.clear and permissions.edit,
        blockers=blockers.reasons,
        outcome=outcome,
        generated_at=format_instant(now),
        scope_href="" if blockers.scope_ok else f"{WIZARD_PATH}/{SCOPE_STEP}",
        start_href=f"{WIZARD_PATH}/{START_BLOCK}",
    )


def build_detail(store: UiStore, name: str, permissions: Permissions) -> DocumentDetail:
    status = document_status(store, name)
    report = _report(store)
    base = document_rows(store, [status])[0]
    meta = _meta_of(store, status)
    history, _ = scan_approvals(store, name)
    row = _row(status, base, report, permissions, meta, _last_approval(store, status))
    text, truncated = (
        _read_text(store, name) if status.state is not DocumentState.NOT_GENERATED else ("", False)
    )
    return DocumentDetail(
        row=row,
        text=text,
        truncated=truncated,
        clauses=tuple(ClauseRow(c.id, c.origin, c.section) for c in meta.clauses) if meta else (),
        measures=tuple(
            MeasureRow(m.id, tuple(m.covered_clauses), tuple(m.missing_clauses))
            for m in meta.measures
        )
        if meta
        else (),
        history=tuple(info for a in history if (info := _approval(a))),
        unreadable=tuple(status.unreadable),
    )


# --- handlers ------------------------------------------------------------------------------------


def _saved(request: Request) -> str | None:
    value = request.query_params.get("hecho", "")
    return value if value in SAVED_VALUES else None


def _list_page(
    request: Request,
    store: UiStore,
    *,
    status: int = 200,
    saved: str | None = None,
    outcome: GenerateOutcome | None = None,
    form_errors: list[str] | None = None,
    errors: dict[str, list[str]] | None = None,
    values: dict[str, str] | None = None,
) -> Response:
    page = build_page(store, request.app.state.clock(), permissions_of(request), outcome)
    return render(
        request,
        "views/documentos.html",
        status,
        **page_context(request, "documentos", "Documentos"),
        page=page,
        saved=saved,
        form_errors=form_errors or [],
        errors=errors or {},
        values=values or {},
    )


def listado(request: Request) -> Response:
    outcome = None
    token = request.query_params.get("resultado", "")
    if request.method == "GET" and token:  # un HEAD o un GET sin token no gastan nada
        outcome = request.app.state.outcomes.take(
            identity_of(request).email, token, request.app.state.clock()
        )
    return _list_page(request, request.app.state.store, saved=_saved(request), outcome=outcome)


def ficha(request: Request) -> Response:
    name = request.path_params["documento"]
    if name not in document_names():  # antes de derivar ninguna clave del segmento
        raise HTTPException(404)
    detail = build_detail(request.app.state.store, name, permissions_of(request))
    if detail.row.state == DocumentState.NOT_GENERATED.value:  # el listado dice por qué
        raise HTTPException(404)
    return render(
        request,
        "views/documento.html",
        **page_context(request, "documentos", titles()[name]),
        detail=detail,
    )


# --- escritura -----------------------------------------------------------------------------------


def _audit(request: Request, action: str, keys: list[str], *, failed: bool = False) -> None:
    audit_write(
        identity=identity_of(request).email,
        step=STEP,
        action=action,
        keys=keys,
        at=request.app.state.clock(),
        roles=[r.value for r in permissions_of(request).used],
        failed=failed,
    )


def _audit_rejected(request: Request, action: str) -> None:
    audit_write(
        identity=identity_of(request).email,
        step=STEP,
        action=action,
        keys=[],
        at=request.app.state.clock(),
        roles=[r.value for r in permissions_of(request).used],
        rejected=True,
    )


async def _written(request: Request, store: UiStore, action: str, work):
    """Ejecuta la escritura; si falla tras escribir algo, audita lo escrito y relanza."""
    try:
        return await run_in_threadpool(work)
    except StepRejected:
        raise
    except Exception:
        if store.written:
            _audit(request, action, store.written, failed=True)
        raise


def _refused(request: Request, action: str, store: UiStore, **page: Any) -> Response:
    _audit_rejected(request, action)
    return _list_page(request, store, status=422, **page)


def _blocker_lines(blockers: GenerationBlockers) -> list[str]:
    return [
        *blockers.reasons,
        *(f"{g.label}: {item}" for g in blockers.groups for item in g.items),
    ]


async def generar(request: Request) -> Response:
    store = request.app.state.store.recording()
    blockers = generation_blockers(store, _report(store))
    if not blockers.clear:
        return _refused(request, "generar", store, form_errors=_blocker_lines(blockers))
    try:
        report = await _written(request, store, "generar", lambda: build_documents(store))
    except ScopeNotApproved:  # el alcance cambió entre el cálculo y la generación
        return _refused(request, "generar", store, form_errors=[SCOPE_NOT_APPROVED])
    except AnswersInvalid as exc:
        groups = _groups(issue_groups(exc.report))
        lines = [INVALID_ANSWERS, *_blocker_lines(GenerationBlockers(True, True, (), groups))]
        return _refused(request, "generar", store, form_errors=lines)
    _audit(request, "generar", store.written)
    token = request.app.state.outcomes.put(
        identity_of(request).email, generate_outcome(report), request.app.state.clock()
    )
    return RedirectResponse(f"{LIST_PATH}?resultado={token}#resultado", status_code=303)


def _echo(key: str, by: str) -> dict[str, str]:
    """Lo que la persona escribió, para repintarlo; lo que parece un secreto no se devuelve."""
    return {} if find_secrets({"valor": by}) else {key: by}


def _unechoed(line: str) -> str:
    """Una línea del producto que cita lo escrito: si parece un secreto no se devuelve."""
    return "No se puede aprobar con ese nombre." if find_secrets({"valor": line}) else line


def _approve(store: UiStore, name: str, by: str, role, now: datetime) -> None:
    try:
        approve_document(store, name, by, role, now)
    except ScopeNotApproved:
        raise StepRejected(form_errors=[SCOPE_NOT_APPROVED]) from None
    except UnknownDocument:
        raise StepRejected(form_errors=[UNKNOWN_DOCUMENT]) from None
    except DocumentApprovalRejected as exc:
        raise StepRejected(form_errors=[_unechoed(plain(e)) for e in exc.errors]) from None


async def aprobar(request: Request) -> Response:
    store, now = request.app.state.store.recording(), request.app.state.clock()
    name = request.path_params["documento"]
    if name not in document_names():  # antes de derivar ninguna clave del segmento
        return _refused(request, "aprobar", store, form_errors=[UNKNOWN_DOCUMENT])
    try:
        form = await read_form(request)
    except FormTooLarge:
        _audit_rejected(request, "aprobar")
        return error_page(request, 413, "El formulario es demasiado grande.")
    except FormInvalid:
        _audit_rejected(request, "aprobar")
        return error_page(request, 400, "No se ha entendido el formulario.")
    by, key = first(form, "approved_by"), f"approved_by~{name}"
    try:
        declared_name(by, "Quién lo aprueba", key)
        role = required_role(name, _report(store).values)
        if role is None:
            raise StepRejected(form_errors=[NO_AUTHORITY])
        permissions_of(request).require(role)
        await _written(request, store, "aprobar", lambda: _approve(store, name, by, role, now))
    except StepRejected as rejected:
        return _refused(
            request,
            "aprobar",
            store,
            form_errors=rejected.form_errors,
            errors=rejected.errors,
            values=_echo(key, by),
        )
    _audit(request, "aprobar", store.written)
    return RedirectResponse(f"{LIST_PATH}?hecho=aprobado#doc-{name}", status_code=303)


DOCUMENTS_ROUTES: list[BaseRoute] = [
    Route(LIST_PATH, listado, methods=["GET"]),
    Route(f"{LIST_PATH}/generar", generar, methods=["POST"]),
    Route(f"{LIST_PATH}/{{documento}}", ficha, methods=["GET"]),
    # `:path`: un nombre con barras llega al handler, que lo rechaza con el mensaje fijo y lo audita
    Route(f"{LIST_PATH}/{{documento:path}}/aprobar", aprobar, methods=["POST"]),
]
