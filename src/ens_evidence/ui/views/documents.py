"""Documentos organizativos: estado de cada uno, fuente, aprobación y qué falta para generarlos.

Solo lee. La lógica es la del asistente (`wizard/docs_shared.py`) y la de las APIs de producto
(`orgdocs`); aquí solo se presenta. La plantilla recibe únicamente los DTO de este módulo.
"""

from dataclasses import dataclass
from datetime import datetime
from functools import cache

import yaml
from pydantic import ValidationError
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import BaseRoute, Route

from ens_evidence.approvals import safe_load_unique
from ens_evidence.orgdocs import (
    ANSWERS_KEY,
    BuildReport,
    DocumentState,
    DocumentStatus,
    all_statuses,
    document_names,
    load_templates,
    validate_answers,
)
from ens_evidence.orgdocs.answers import AnswersReport
from ens_evidence.orgdocs.approval import (
    DocumentApproval,
    document_status,
    required_role,
    scan_approvals,
)
from ens_evidence.orgdocs.build import BuildStatus
from ens_evidence.orgdocs.keys import document_key, meta_key
from ens_evidence.orgdocs.meta import DocumentMeta
from ens_evidence.scope import ScopeState, scope_status
from ens_evidence.ui.pages import page_context, permissions_of, render
from ens_evidence.ui.roles import Permissions, role_options
from ens_evidence.ui.store import UiStore
from ens_evidence.ui.views import format_instant
from ens_evidence.ui.wizard.base import read_yaml
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
SAVED_VALUES = frozenset({"aprobado"})


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
class GenerateOutcome:
    created: tuple[str, ...]
    updated: tuple[str, ...]
    unchanged: tuple[str, ...]
    archived: tuple[str, ...]
    """Documentos editados a mano cuya versión anterior se archivó (también figuran en updated)."""


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
    return validate_answers(read_yaml(store, ANSWERS_KEY), load_templates())


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
    def names(*kinds: BuildStatus) -> tuple[str, ...]:
        return tuple(d.name for d in report.documents if d.status in kinds)

    return GenerateOutcome(
        created=names(BuildStatus.CREATED),
        updated=names(BuildStatus.UPDATED),
        unchanged=names(BuildStatus.UNCHANGED),
        archived=tuple(d.name for d in report.documents if d.edited_by_hand),
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
        last_approval=_approval(status.approval),
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
        role_notice=role_notice if can_approve else "",
        can_submit=can_approve and role is not None and not role_missing,
    )


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
        _row(status, base, report, permissions, _meta_of(store, status))
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
    row = _row(status, base, report, permissions, meta)
    history, _ = scan_approvals(store, name)
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
        history=tuple(info for a in reversed(history) if (info := _approval(a))),
        unreadable=tuple(status.unreadable),
    )


# --- handlers ------------------------------------------------------------------------------------


def _saved(request: Request) -> str | None:
    value = request.query_params.get("hecho", "")
    return value if value in SAVED_VALUES else None


def listado(request: Request) -> Response:
    page = build_page(request.app.state.store, request.app.state.clock(), permissions_of(request))
    return render(
        request,
        "views/documentos.html",
        **page_context(request, "documentos", "Documentos"),
        page=page,
        rows=page.rows,
        saved=_saved(request),
        form_errors=[],
    )


def ficha(request: Request) -> Response:
    name = request.path_params["documento"]
    if name not in document_names():  # antes de derivar ninguna clave del segmento
        raise HTTPException(404)
    detail = build_detail(request.app.state.store, name, permissions_of(request))
    return render(
        request,
        "views/documento.html",
        **page_context(request, "documentos", titles()[name]),
        detail=detail,
        row=detail.row,
    )


DOCUMENTS_ROUTES: list[BaseRoute] = [
    Route(LIST_PATH, listado, methods=["GET"]),
    Route(f"{LIST_PATH}/{{documento}}", ficha, methods=["GET"]),
]
