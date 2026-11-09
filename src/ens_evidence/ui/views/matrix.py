"""Matriz de medidas: la lista y, por medida, responsable, enlaces y revisión."""

import re
from collections.abc import Callable
from datetime import datetime
from functools import cache

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import BaseRoute, Route

from ens_evidence.catalog import load_catalog
from ens_evidence.categorization import CategorizationState, categorization_status
from ens_evidence.checks.source import StoredCheckResults
from ens_evidence.evidence import list_manifests
from ens_evidence.matrix import (
    COMPENSATION,
    MATRIX_KEY,
    MatrixRejected,
    RowKind,
    RowStatus,
    add_link,
    build_link,
    documents_snapshot,
    init_matrix,
    matrix_status,
    prepare_review,
    record_review,
    remove_link,
    set_entry,
)
from ens_evidence.matrix.evidence import is_linkable
from ens_evidence.matrix.links import CHECK, DOCUMENT, EVIDENCE, KIND_LABELS
from ens_evidence.matrix.model import MAX_REVIEW_EVERY_DAYS
from ens_evidence.matrix.review import uncovered_message
from ens_evidence.models.dossier import SELF_ASSESSORS
from ens_evidence.models.enums import MeasureAssessment, Role
from ens_evidence.models.readiness import BlockerKind
from ens_evidence.privacy_text import check_declared_name
from ens_evidence.scope import ScopeState, scope_status
from ens_evidence.ui.audit import audit_write
from ens_evidence.ui.forms import Form, FormInvalid, FormTooLarge, first, read_form
from ens_evidence.ui.pages import error_page, identity_of, page_context, render
from ens_evidence.ui.plain import CLI_TRACE, translate
from ens_evidence.ui.store import UiStore
from ens_evidence.ui.views import BLOCKER_LABELS, format_instant
from ens_evidence.ui.views.tools import copias_offer
from ens_evidence.workspace import WorkspaceError

ROLE_LABELS = {
    Role.SYSTEM_ADMINISTRATOR: "Administrador del sistema",
    Role.SYSTEM_ADMINISTRATOR_DELEGATE: "Delegado del administrador",
    Role.SECURITY_OFFICER: "Responsable de la seguridad",
    Role.SYSTEM_OWNER: "Responsable del sistema",
    Role.MANAGEMENT: "Dirección",
    Role.INFORMATION_OWNER: "Responsable de la información",
    Role.SERVICE_OWNER: "Responsable del servicio",
}
REVIEW_ROLES = [r for r in Role if r in SELF_ASSESSORS]
ASSESSMENT_LABELS = {
    MeasureAssessment.COMPLIANT: "Favorable: la medida está implantada",
    MeasureAssessment.NON_COMPLIANT: "Desfavorable: la medida no está implantada",
    MeasureAssessment.REQUIRES_REVIEW: "Pide revisión: falta información para valorarla",
}
LINK_KINDS = {"evidence": EVIDENCE, "document": DOCUMENT}
MAX_EVIDENCE_OFFERED = 50
PENDING_REVIEW_DETAILS = {
    MeasureAssessment.NOT_ASSESSED.value,
    MeasureAssessment.REQUIRES_REVIEW.value,
}
KNOWN_PROBLEMS = (
    ("falta la periodicidad", "Falta la periodicidad de revisión: fíjala en «Responsable»."),
    ("falta el responsable", "Falta el responsable: indícalo en «Responsable»."),
)
GENERIC_PROBLEM = "La revisión no se puede registrar todavía."
BY_LABEL = "Quién revisa"


class Rejected(Exception):
    """El formulario no se aplica: `errors` en lenguaje llano; `uncovered`, cláusulas sin cubrir."""

    def __init__(self, *errors: str, uncovered: tuple[str, ...] = ()) -> None:
        super().__init__("formulario rechazado")
        self.errors = list(errors)
        self.uncovered = uncovered


@cache
def measure_names() -> dict[str, str]:
    return {card.id: card.name for card in load_catalog().measures}


def plain(message: str) -> str:
    """Un texto de producto sin órdenes de la línea de órdenes."""
    for prefix, text in KNOWN_PROBLEMS:
        if message.lower().startswith(prefix):
            return text
    if not CLI_TRACE.search(message):
        return message
    cleaned = translate(message).strip()
    if not cleaned or CLI_TRACE.search(cleaned):
        head = re.split(r"[:(;`]|--", message, maxsplit=1)[0].strip().rstrip(".")
        cleaned = head if head and not CLI_TRACE.search(head) else GENERIC_PROBLEM
    return cleaned if cleaned.endswith(".") else f"{cleaned}."


def _ensure_matrix(store: UiStore, now: datetime, email: str, *, force: bool = False) -> None:
    """Crea o completa `controls/matrix.yaml`; sin Declaración vigente no hace nada."""
    if store.exists(MATRIX_KEY) and not force:
        return
    recording = store.recording()
    try:
        init_matrix(recording, now)
    except (MatrixRejected, WorkspaceError):
        return
    if recording.written:
        audit_write(identity=email, step="matriz", action="iniciar", keys=recording.written, at=now)


def _status(store: UiStore, now: datetime) -> tuple[RowStatus, ...]:
    return matrix_status(store, now, checks=StoredCheckResults(reference_now=now))[1]


def matriz(request: Request) -> Response:
    store, now = request.app.state.store, request.app.state.clock()
    _ensure_matrix(store, now, identity_of(request).email)
    names = measure_names()
    wizard_pending = (
        scope_status(store).state is not ScopeState.APPROVED
        or categorization_status(store, now).state is not CategorizationState.CURRENT
    )
    return render(
        request,
        "views/matriz.html",
        **page_context(request, "matriz", "Matriz de medidas"),
        rows=[
            {
                "measure_id": r.row.spec.measure_id,
                "title": names.get(r.row.spec.measure_id, ""),
                "applicability": r.row.measure.applicability.value,
                "assessment": r.row.measure.assessment.value,
                "evidence_count": sum(1 for s in r.row.supports if s.counts),
            }
            for r in _status(store, now)
        ],
        wizard_pending=wizard_pending,
        generated_at=format_instant(now),
    )


# --- la página de una medida ---------------------------------------------------------------------


def _row_of(store: UiStore, now: datetime, email: str, measure_id: str) -> RowStatus:
    def find() -> RowStatus | None:
        return next((r for r in _status(store, now) if r.row.spec.measure_id == measure_id), None)

    found = find()
    if found is not None and found.row.entry is None:
        _ensure_matrix(store, now, email, force=True)
        found = find()
    if found is None:
        raise HTTPException(404)
    return found


def _is_selected(status: RowStatus) -> bool:
    return status.row.spec.kind is RowKind.SELECTED


def _source_label(kind: str, target: str) -> str:
    return f"{KIND_LABELS.get(kind, kind)} {target}"


def _obligations(status: RowStatus) -> list[dict]:
    counting = [s for s in status.row.supports if s.counts]
    return [
        {
            "id": obligation.id,
            "title": "Compensación" if obligation.id == COMPENSATION else obligation.id,
            "compensating": list(obligation.compensating_measures),
            "clauses": [
                {
                    "unit": clause.id or obligation.id,
                    "label": clause.label or clause.id or obligation.id,
                    "text": clause.text,
                    "sources": [
                        _source_label(*s.link.target)
                        for s in counting
                        if (clause.id or obligation.id) in s.clauses
                    ],
                }
                for clause in obligation.clauses
            ],
        }
        for obligation in status.row.spec.obligations
    ]


def _units(status: RowStatus) -> list[str]:
    return [u for o in status.row.spec.obligations for u in o.units]


def _links(status: RowStatus) -> list[dict]:
    entry = status.row.entry
    by_target = {s.link.target: s for s in status.row.supports}
    return [
        {
            "kind": link.target[0],
            "target": link.target[1],
            "label": _source_label(*link.target),
            "covers": list(link.covers),
            "counts": link.target in by_target and by_target[link.target].counts,
        }
        for link in (entry.links if entry else ())
    ]


def _document_offers(store: UiStore, now: datetime, status: RowStatus) -> list[dict]:
    """Documentos aprobados que, según su ficha, cubren cláusulas de esta medida."""
    units = _units(status)
    offers = []
    for name, document in sorted(documents_snapshot(store, now).documents.items()):
        coverage = document.measures.get(status.row.spec.measure_id)
        if document.state != "APPROVED" or coverage is None:
            continue
        if covered := [u for u in units if u in coverage.covered_clauses]:
            offers.append({"name": name, "covers": covered})
    return offers


def _evidence_offers(store: UiStore, now: datetime) -> list[dict]:
    manifests = [m for m in list_manifests(store).manifests if is_linkable(m)]
    manifests.sort(key=lambda m: m.collected_at, reverse=True)
    return [
        {
            "id": m.id,
            "asset_id": m.asset_id,
            "collected_at": format_instant(m.collected_at),
            "expired": bool(m.valid_until and m.valid_until <= now),
        }
        for m in manifests[:MAX_EVIDENCE_OFFERED]
    ]


def _blockers(status: RowStatus) -> list[str]:
    texts: list[str] = []
    for blocker in status.row.blockers:
        if blocker.kind is BlockerKind.NOT_COMPLIANT and blocker.detail in PENDING_REVIEW_DETAILS:
            text = "Pendiente de revisión"
        else:
            text = BLOCKER_LABELS[blocker.kind]
        clause = blocker.detail.partition(":")[2] if blocker.detail.startswith("clause:") else ""
        text = f"{text}: {clause}" if clause else text
        if text not in texts:
            texts.append(text)
    return texts


def _last_review(status: RowStatus) -> dict | None:
    if status.row.review is None:
        return None
    review = status.row.review.record.review
    return {
        "by": review.reviewer,
        "role": ROLE_LABELS[review.role],
        "at": format_instant(review.reviewed_at),
        "valid_until": format_instant(review.valid_until),
        "conclusion": review.conclusion,
    }


def _detail_page(
    request: Request,
    store: UiStore,
    now: datetime,
    measure_id: str,
    *,
    values: Form | None = None,
    form_errors: list[str] | None = None,
    uncovered: tuple[str, ...] = (),
    saved: bool = False,
    status_code: int = 200,
) -> Response:
    email = identity_of(request).email
    status = _row_of(store, now, email, measure_id)
    entry = status.row.entry
    selected = _is_selected(status)
    echo = {k: v[0] for k, v in (values or {}).items() if v}
    return render(
        request,
        "views/matriz_medida.html",
        status_code=status_code,
        **page_context(request, "matriz", measure_id),
        measure_id=measure_id,
        title=measure_names().get(measure_id, ""),
        applicability=status.row.measure.applicability.value,
        assessment=status.row.measure.assessment.value,
        selected=selected,
        owner=(
            {"name": entry.owner.name, "role": ROLE_LABELS[entry.owner.role]}
            if entry and entry.owner
            else None
        ),
        every_days=entry.review_every_days if entry else None,
        obligations=_obligations(status),
        links=_links(status),
        document_offers=_document_offers(store, now, status) if selected else [],
        evidence_offers=_evidence_offers(store, now) if selected else [],
        units=_units(status),
        blockers=_blockers(status),
        kit_offer=copias_offer(store, measure_id),
        last_review=_last_review(status),
        owner_roles=[{"value": r.value, "label": ROLE_LABELS[r]} for r in Role],
        review_roles=[{"value": r.value, "label": ROLE_LABELS[r]} for r in REVIEW_ROLES],
        assessments=[{"value": a.value, "label": ASSESSMENT_LABELS[a]} for a in ASSESSMENT_LABELS],
        max_days=MAX_REVIEW_EVERY_DAYS,
        generated_at=format_instant(now),
        by_default=echo.get("by", ""),
        values=echo,
        form_errors=form_errors or [],
        uncovered=list(uncovered),
        saved=saved,
    )


def medida(request: Request) -> Response:
    return _detail_page(
        request,
        request.app.state.store,
        request.app.state.clock(),
        request.path_params["medida"],
        saved=request.query_params.get("guardado") == "1",
    )


# --- escrituras ----------------------------------------------------------------------------------

Apply = Callable[[UiStore, datetime, str, str, Form], None]


def _set_owner(store: UiStore, now: datetime, measure_id: str, email: str, form: Form) -> None:
    name, role, days = first(form, "owner_name"), first(form, "owner_role"), first(form, "every")
    if not name and not days:
        raise Rejected("Indica el responsable, la periodicidad o ambos.")
    errors = []
    if name and role not in {r.value for r in Role}:
        errors.append("Elige el rol del responsable.")
    if days and not (days.isdigit() and 1 <= int(days) <= MAX_REVIEW_EVERY_DAYS):
        errors.append(f"La periodicidad son días, de 1 a {MAX_REVIEW_EVERY_DAYS}.")
    if errors:
        raise Rejected(*errors)
    try:
        set_entry(
            store,
            now,
            measure_id,
            owner_name=name or None,
            owner_role=role if name else None,
            every_days=int(days) if days else None,
        )
    except MatrixRejected as exc:
        raise Rejected(*map(plain, exc.errors)) from None


def _link(store: UiStore, now: datetime, measure_id: str, email: str, form: Form) -> None:
    kind = LINK_KINDS.get(first(form, "kind"))
    target = first(form, "target")
    covers = [c.strip() for c in form.get("covers", []) if c.strip()]
    errors = []
    if kind is None or not target:
        errors.append("Elige qué documento o evidencia enlazar.")
    if not covers:
        errors.append("Marca al menos una cláusula que cubra.")
    if errors or kind is None:
        raise Rejected(*errors)
    try:
        add_link(store, now, measure_id, build_link(kind, target, covers))
    except MatrixRejected as exc:
        raise Rejected(*map(plain, exc.errors)) from None


def _unlink(store: UiStore, now: datetime, measure_id: str, email: str, form: Form) -> None:
    kind, target = first(form, "kind"), first(form, "target")
    if kind not in {EVIDENCE, CHECK, DOCUMENT} or not target:
        raise Rejected("Elige qué enlace quitar.")
    try:
        remove_link(store, now, measure_id, kind, target)
    except MatrixRejected as exc:
        raise Rejected(*map(plain, exc.errors)) from None


def _review(store: UiStore, now: datetime, measure_id: str, email: str, form: Form) -> None:
    by, role = first(form, "by"), first(form, "role")
    conclusion, assessment = first(form, "conclusion"), first(form, "assessment")
    selected = _is_selected(_row_of(store, now, email, measure_id))
    errors = []
    if not by:
        errors.append("Indica quién revisa.")
    else:
        errors += (plain(e) for e in check_declared_name(by, BY_LABEL))
    if role not in {r.value for r in REVIEW_ROLES}:
        errors.append("Elige el rol de quien revisa.")
    if not conclusion:
        errors.append("Escribe la conclusión de la revisión.")
    if selected and assessment not in {a.value for a in ASSESSMENT_LABELS}:
        errors.append("Elige la valoración.")
    if errors:
        raise Rejected(*errors)
    try:
        plan = prepare_review(
            store,
            now,
            measure_id,
            by=by,
            role=role,
            conclusion=conclusion,
            assessment=assessment if selected else None,
            checks=StoredCheckResults(reference_now=now),
        )
    except MatrixRejected as exc:
        raise Rejected(*map(plain, exc.errors)) from None
    if plan.problems:
        gap_texts = {uncovered_message(g) for g in plan.uncovered}
        others = [plain(p) for p in plan.problems if p not in gap_texts]
        summary = ["No se puede dar por favorable: hay cláusulas sin cubrir."] if gap_texts else []
        raise Rejected(*others, *summary, uncovered=tuple(g.unit for g in plan.uncovered))
    record_review(store, plan)


ACTIONS: dict[str, Apply] = {
    "responsable": _set_owner,
    "enlazar": _link,
    "desenlazar": _unlink,
    "revisar": _review,
}


async def escribir(request: Request) -> Response:
    name = request.path_params["accion"]
    apply = ACTIONS.get(name)
    if apply is None:
        raise HTTPException(404)
    measure_id = request.path_params["medida"]
    try:
        form = await read_form(request)
    except FormTooLarge:
        return error_page(request, 413, "El formulario es demasiado grande.")
    except FormInvalid:
        return error_page(request, 400, "No se ha entendido el formulario.")
    now, email = request.app.state.clock(), identity_of(request).email
    store: UiStore = request.app.state.store.recording()
    try:
        apply(store, now, measure_id, email, form)
    except Rejected as rejected:
        return _detail_page(
            request,
            request.app.state.store,
            now,
            measure_id,
            values=form,
            form_errors=rejected.errors,
            uncovered=rejected.uncovered,
            status_code=422,
        )
    if store.written:
        audit_write(identity=email, step="matriz", action=name, keys=store.written, at=now)
    return RedirectResponse(f"/matriz/{measure_id}?guardado=1", status_code=303)


MATRIX_ROUTES: list[BaseRoute] = [
    Route("/matriz", matriz, methods=["GET"]),
    Route("/matriz/{medida}", medida, methods=["GET"]),
    Route("/matriz/{medida}/{accion}", escribir, methods=["POST"]),
]
