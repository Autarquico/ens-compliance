"""Expediente: generarlo, ver su estado y la siguiente transición, registrarla y descargarlo.

Las mismas APIs que `assessment dossier …`; la identidad de Access prellena «quién» y se puede
cambiar (el nombre es declarado, no se autentica).
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import BaseRoute, Route

from ens_evidence.assessment.dossier import (
    DossierRejected,
    TransitionRejected,
    dossier_status,
    generate_dossier,
    record_transition,
    scan_transitions,
)
from ens_evidence.assessment.dossier.derived import has_markdown
from ens_evidence.assessment.dossier.model import DossierSnapshot, SectionState
from ens_evidence.assessment.dossier.status import incomplete_sections
from ens_evidence.assessment.dossier.store import (
    ASSESSMENTS_PREFIX,
    DOSSIER_ID,
    MARKDOWN_NAME,
    DossierUnreadable,
    list_dossiers,
    load_snapshot,
)
from ens_evidence.categorization import CategorizationNotCurrent
from ens_evidence.incidents.privacy import check_declared_name
from ens_evidence.matrix.messages import role_label
from ens_evidence.models.dossier import CorrectiveMeasure
from ens_evidence.models.enums import Category, DossierState, Role
from ens_evidence.scope import ScopeNotApproved
from ens_evidence.ui.audit import audit_write
from ens_evidence.ui.forms import Form, FormInvalid, FormTooLarge, first, read_form
from ens_evidence.ui.pages import error_page, identity_of, page_context, render
from ens_evidence.ui.store import UiStore
from ens_evidence.ui.views import NO_DATE, format_instant
from ens_evidence.workspace import AlreadyExists

NOT_GENERATED = "Sin generar"
BROKEN = "ROTO"
STEP = "expediente"

SECTION_LABELS = {
    SectionState.RECORDED: "Registrada",
    SectionState.INCOMPLETE: "Incompleta",
    SectionState.PENDING_HUMAN_REVIEW: "Pendiente de revisión humana",
}
SELF_ASSESS_ROLES = {
    Role.SYSTEM_ADMINISTRATOR.value: "Administrador del sistema",
    Role.SYSTEM_ADMINISTRATOR_DELEGATE.value: "Delegado del administrador del sistema",
}
ACTIONS = {
    "self-assess": DossierState.SELF_ASSESSED,
    "analyze": DossierState.ANALYZED,
    "declare": DossierState.DECLARED,
}
NEXT_ACTION = {
    DossierState.GENERATED: "self-assess",
    DossierState.SELF_ASSESSED: "analyze",
    DossierState.ANALYZED: "declare",
}
NEXT_TEXT = {
    "self-assess": (
        "Toca la autoevaluación: la hace el administrador del sistema (o su delegado) después "
        "de revisar cada medida."
    ),
    "analyze": (
        "Toca el análisis del responsable de la seguridad, que eleva sus conclusiones al "
        "responsable del sistema."
    ),
    "declare": (
        "Toca decidir a la dirección. Si la organización declara, aquí solo queda registrado el "
        "acto: la herramienta no redacta la declaración ni genera el distintivo."
    ),
}
ANALYZED_NOT_BASIC = (
    "En categoría MEDIA o ALTA la declaración no la hace la propia organización (art. 38.1): "
    "hace falta una auditoría externa. Este es el último paso que registra la herramienta."
)
DECLARED_NOTE = (
    "La declaración ya está registrada. Darle publicidad corresponde a la organización "
    "(art. 38.2 del RD 311/2022, según la ITS aplicable)."
)
CATEGORY_NAMES = {Category.BASICA: "BÁSICA", Category.MEDIA: "MEDIA", Category.ALTA: "ALTA"}
REQUIRED = {
    "self-assess": {"by": "Quién la registra", "role": "El rol"},
    "analyze": {
        "by": "Quién la registra",
        "conclusion": "Las conclusiones",
        "elevated_to": "A quién se elevan",
    },
    "declare": {"by": "Quién declara"},
}


BY_LABEL = "Quién lo registra"
STATE_WORDS = {
    "SELF_ASSESSED": "la autoevaluación",
    "ANALYZED": "el análisis",
    "DECLARED": "la declaración",
    "GENERATED": "el expediente generado",
}


SAVED_TEXTS = {
    "generado": "Expediente generado.",
    "self-assess": "Autoevaluación registrada.",
    "analyze": "Análisis registrado.",
    "declare": "Declaración registrada.",
}


class Rejected(Exception):
    """Lo que no se pudo hacer, en lenguaje llano."""

    def __init__(self, *messages: str, errors: dict[str, list[str]] | None = None) -> None:
        super().__init__("rechazado")
        self.messages = list(messages)
        self.errors = errors or {}


# --- lectura -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Shown:
    id: str
    snapshot: DossierSnapshot


def _readable(store: UiStore) -> list[Shown]:
    return [Shown(d.id, d.snapshot) for d in list_dossiers(store) if d.snapshot is not None]


def _section_rows(snapshot: DossierSnapshot) -> list[dict]:
    generated = format_instant(snapshot.inputs.generated_at)
    return [
        {
            "title": f"{s.number}. {s.title}",
            "file": s.sources[0].key if s.sources else "—",
            "state": s.state.value,
            "state_label": SECTION_LABELS[s.state],
            "generated_at": generated,
            "fingerprint": s.sources[0].sha256 if s.sources else None,
        }
        for s in snapshot.sections
    ]


def _next_text(action: str | None, state: DossierState | None, category: Category) -> str:
    if state is DossierState.DECLARED:
        return DECLARED_NOTE
    if state is DossierState.ANALYZED and category is not Category.BASICA:
        return ANALYZED_NOT_BASIC
    return NEXT_TEXT.get(action or "", "")


def _transitions(scan) -> list[dict]:
    if scan.dossier is None:
        return []
    return [
        {
            "state": t.to_state.value,
            "actor": t.actor,
            "role": role_label(t.role),
            "at": format_instant(t.at),
        }
        for t in scan.dossier.transitions
    ]


def _detail(store: UiStore, now: datetime, shown: Shown) -> dict[str, Any]:
    scan = scan_transitions(store, shown.id)
    status = dossier_status(store, shown.id, now=now)
    state = scan.dossier.state if scan.dossier else None
    category = shown.snapshot.category
    action = NEXT_ACTION.get(state) if state else None
    if action == "declare" and category is not Category.BASICA:
        action = None
    if status.integrity or state is None:
        action = None
    declared = (
        next((t for t in scan.dossier.transitions if t.to_state is DossierState.DECLARED), None)
        if scan.dossier
        else None
    )
    drift = (
        []
        if status.drift is None or status.drift.current
        else [
            f"{section}: {line}" for section, lines in status.drift.drift.items() for line in lines
        ]
    )
    return {
        "dossier_id": shown.id,
        "category": CATEGORY_NAMES[category],
        "dossier_state": state.value if state else BROKEN,
        "generated_at": format_instant(shown.snapshot.inputs.generated_at),
        "declared": {"by": declared.actor, "at": format_instant(declared.at)} if declared else None,
        "sections": _section_rows(shown.snapshot),
        "transitions": _transitions(scan),
        "stale": status.drift is not None and not status.drift.current,
        "drift_lines": drift[:10],
        "integrity": list(status.integrity),
        "blocked": list(status.declaration_blocked) if action == "declare" else [],
        "incomplete": list(incomplete_sections(shown.snapshot)),
        "readiness_notice": status.readiness_notice,
        "action": action,
        "next_text": _next_text(action, state, category),
        "can_download": has_markdown(store, shown.id),
        "self_assess_roles": SELF_ASSESS_ROLES,
    }


EMPTY = {
    "dossier_id": None,
    "category": None,
    "dossier_state": NOT_GENERATED,
    "generated_at": NO_DATE,
    "declared": None,
    "sections": [],
    "transitions": [],
    "stale": False,
    "drift_lines": [],
    "integrity": [],
    "blocked": [],
    "incomplete": [],
    "readiness_notice": None,
    "action": None,
    "next_text": "",
    "can_download": False,
    "self_assess_roles": SELF_ASSESS_ROLES,
}


def _page(
    request: Request,
    shown: Shown | None,
    *,
    form_errors: list[str] | None = None,
    errors: dict[str, list[str]] | None = None,
    values: dict[str, Any] | None = None,
    saved: str | None = None,
    status_code: int = 200,
) -> Response:
    store, now = request.app.state.store, request.app.state.clock()
    detail = _detail(store, now, shown) if shown else EMPTY
    return render(
        request,
        "views/expediente.html",
        status_code=status_code,
        **page_context(request, "expediente", "Expediente"),
        **detail,
        form_errors=form_errors or [],
        errors=errors or {},
        values={"by": "", **(values or {})},
        saved=SAVED_TEXTS.get(saved or ""),
    )


def _find(store: UiStore, dossier: str) -> Shown:
    if not DOSSIER_ID.fullmatch(dossier):
        raise HTTPException(404)
    try:
        return Shown(dossier, load_snapshot(store, dossier))
    except DossierUnreadable:
        raise HTTPException(404) from None


def expediente(request: Request) -> Response:
    store = request.app.state.store
    readable = _readable(store)
    saved = request.query_params.get("hecho")
    return _page(request, readable[-1] if readable else None, saved=saved)


def expediente_por_id(request: Request) -> Response:
    shown = _find(request.app.state.store, request.path_params["dossier"])
    return _page(request, shown, saved=request.query_params.get("hecho"))


def descargar(request: Request) -> Response:
    store = request.app.state.store
    shown = _find(store, request.path_params["dossier"])
    if not has_markdown(store, shown.id):
        raise HTTPException(404)
    body = store.read_bytes(f"{ASSESSMENTS_PREFIX}/{shown.id}/{MARKDOWN_NAME}")
    return Response(
        body,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="expediente-{shown.id}.md"'},
    )


# --- escritura ---------------------------------------------------------------------------------


def generate(store: UiStore, now: datetime) -> str:
    """Lo mismo que `assessment dossier generate`; devuelve el id."""
    try:
        generated = generate_dossier(store, now)
    except ScopeNotApproved:
        raise Rejected("Antes de generar el expediente hay que aprobar el alcance.") from None
    except CategorizationNotCurrent:
        raise Rejected(
            "Antes de generar el expediente hay que tener la categorización vigente."
        ) from None
    except DossierRejected as exc:
        raise Rejected("No se genera el expediente:", *exc.errors) from None
    except AlreadyExists:
        raise Rejected(
            "Ya existe un expediente con ese contenido y ese instante; vuelve a intentarlo."
        ) from None
    if generated.package_errors:
        raise Rejected("El paquete por medida no se ha guardado:", *generated.package_errors)
    return generated.dossier


def _by(form: Form, action: str) -> dict[str, str]:
    values = {name: first(form, name) for name in REQUIRED[action]}
    missing = {name: [f"{label} es obligatorio."] for name, label in REQUIRED[action].items()
               if not values[name]}  # fmt: skip
    if not missing and (problems := check_declared_name(values["by"], BY_LABEL)):
        missing = {"by": problems}
    if missing:
        raise Rejected(*(m for messages in missing.values() for m in messages), errors=missing)
    return values


def _details(action: str, form: Form, values: dict[str, str]) -> tuple[Role, dict[str, Any]]:
    if action == "self-assess":
        if values["role"] not in SELF_ASSESS_ROLES:
            raise Rejected(
                "Elige el rol.", errors={"role": ["Elige uno de los roles de la lista."]}
            )
        return Role(values["role"]), {}
    if action == "analyze":
        measures = [
            CorrectiveMeasure(description=line.strip(), addressee=Role.SYSTEM_OWNER)
            for line in first(form, "corrective_measures").splitlines()
            if line.strip()
        ]
        return Role.SECURITY_OFFICER, {
            "conclusions": values["conclusion"],
            "elevated_to": values["elevated_to"],
            "elevation_confirmed": "elevated" in form,
            "manual_judgment_review": "manual_judgment_review" in form,
            "corrective_measures": measures,
        }
    return Role.MANAGEMENT, {
        "declaration_evidence_id": first(form, "evidence") or None,
        "acknowledge_incomplete": "acknowledge_incomplete" in form,
    }


def plain_state(message: str) -> str:
    for token, words in STATE_WORDS.items():
        message = message.replace(token, words)
    return message.replace("--by", BY_LABEL)


def transition(store: UiStore, now: datetime, dossier: str, action: str, form: Form) -> None:
    """Lo mismo que `assessment dossier self-assess|analyze|declare`."""
    values = _by(form, action)
    role, details = _details(action, form, values)
    try:
        record_transition(
            store, dossier, ACTIONS[action], by=values["by"], role=role, at=now, now=now, **details
        )
    except TransitionRejected as exc:
        raise Rejected(
            f"No se registra {STATE_WORDS[ACTIONS[action].value]}:", *map(plain_state, exc.errors)
        ) from None


def _audited(request: Request, store: UiStore, action: str, now: datetime) -> None:
    audit_write(
        identity=identity_of(request).email,
        step=STEP,
        action=action,
        keys=store.written,
        at=now,
    )


async def _form(request: Request) -> Form | Response:
    try:
        return await read_form(request)
    except FormTooLarge:
        return error_page(request, 413, "El formulario es demasiado grande.")
    except FormInvalid:
        return error_page(request, 400, "No se ha entendido el formulario.")


def _echo(form: Form) -> dict[str, Any]:
    return {name: values[0] if values else "" for name, values in form.items()}


async def generar(request: Request) -> Response:
    store, now = request.app.state.store.recording(), request.app.state.clock()
    try:
        dossier = await run_in_threadpool(generate, store, now)
    except Rejected as rejected:
        readable = _readable(request.app.state.store)
        return _page(
            request,
            readable[-1] if readable else None,
            form_errors=rejected.messages,
            status_code=422,
        )
    _audited(request, store, "generar", now)
    return RedirectResponse(f"/expediente/{dossier}?hecho=generado", status_code=303)


async def registrar(request: Request) -> Response:
    action = request.path_params["accion"]
    if action not in ACTIONS:
        raise HTTPException(404)
    shown = _find(request.app.state.store, request.path_params["dossier"])
    form = await _form(request)
    if isinstance(form, Response):
        return form
    store, now = request.app.state.store.recording(), request.app.state.clock()
    try:
        await run_in_threadpool(transition, store, now, shown.id, action, form)
    except Rejected as rejected:
        return _page(
            request,
            shown,
            form_errors=rejected.messages,
            errors=rejected.errors,
            values=_echo(form),
            status_code=422,
        )
    _audited(request, store, action, now)
    return RedirectResponse(f"/expediente/{shown.id}?hecho={action}", status_code=303)


DOSSIER_ROUTES: list[BaseRoute] = [
    Route("/expediente", expediente, methods=["GET"]),
    Route("/expediente/generar", generar, methods=["POST"]),
    Route("/expediente/{dossier}", expediente_por_id, methods=["GET"]),
    Route("/expediente/{dossier}/dossier.md", descargar, methods=["GET"]),
    Route("/expediente/{dossier}/{accion}", registrar, methods=["POST"]),
]
