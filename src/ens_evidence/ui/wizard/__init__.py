"""Rutas del asistente: GET muestra el paso, POST lo guarda por las APIs de producto."""

from datetime import datetime
from typing import Any

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import BaseRoute, Route

from ens_evidence.scope.secrets import find_secrets
from ens_evidence.ui.audit import audit_write
from ens_evidence.ui.forms import FormInvalid, FormTooLarge, first, read_form
from ens_evidence.ui.pages import error_page, identity_of, page_context, render
from ens_evidence.ui.store import UiStore
from ens_evidence.ui.wizard.applicability import APPLICABILITY_STEPS
from ens_evidence.ui.wizard.base import Step, StepInput, StepRejected
from ens_evidence.ui.wizard.categorization import CATEGORIZATION_STEPS
from ens_evidence.ui.wizard.docs import DOC_STEPS
from ens_evidence.ui.wizard.risk import RISK_STEPS
from ens_evidence.ui.wizard.scope import SCOPE_STEPS

STEPS: tuple[Step, ...] = (
    *SCOPE_STEPS,
    *CATEGORIZATION_STEPS,
    *RISK_STEPS,
    *APPLICABILITY_STEPS,
    *DOC_STEPS,
)
BY_SLUG = {step.slug: step for step in STEPS}


def _state_of(step: Step, store: UiStore, now: datetime, current: str) -> str:
    if step.slug == current:
        return "current"
    if step.done(store, now):
        return "done"
    return "optional" if step.optional else "todo"


def _states(store: UiStore, now: datetime, current: str) -> list[dict[str, str]]:
    return [
        {
            "slug": s.slug,
            "label": s.label,
            "group": s.group,
            "state": _state_of(s, store, now, current),
        }
        for s in STEPS
    ]


def _echo(form: dict[str, list[str]]) -> dict[str, Any]:
    """Lo enviado, para volver a pintarlo; lo que parece un secreto no se devuelve."""
    safe = {
        name: [v for v in values if not find_secrets({"valor": v})] for name, values in form.items()
    }
    return {name: v if len(v) > 1 else (v[0] if v else "") for name, v in safe.items()}


def _page(
    request: Request,
    step: Step,
    step_input: StepInput,
    *,
    saved: bool = False,
    errors: dict[str, list[str]] | None = None,
    form_errors: list[str] | None = None,
    status_code: int = 200,
) -> Response:
    index = STEPS.index(step)
    context = step.context(step_input)
    values = (
        {**context.pop("values", {}), **_echo(step_input.form)}
        if errors or form_errors
        else context.pop("values", {})
    )
    return render(
        request,
        step.template,
        status_code=status_code,
        **page_context(request, "asistente", step.title),
        **context,
        steps=_states(step_input.store, step_input.now, step.slug),
        step=step.slug,
        step_number=index + 1,
        step_total=len(STEPS),
        step_title=step.title,
        form_action=f"/asistente/{step.slug}",
        prev_step=STEPS[index - 1].slug if index else None,
        next_step=STEPS[index + 1].slug if index + 1 < len(STEPS) else None,
        hx_request=request.headers.get("HX-Request") == "true",
        errors=errors or {},
        form_errors=form_errors or [],
        saved=saved,
        values=values,
    )


def _input(request: Request, form: dict[str, list[str]] | None = None) -> StepInput:
    store: UiStore = request.app.state.store
    return StepInput(
        store=store if form is None else store.recording(),
        now=request.app.state.clock(),
        email=identity_of(request).email,
        query=dict(request.query_params),
        form=form or {},
    )


def _step(request: Request) -> Step | None:
    return BY_SLUG.get(request.path_params["slug"])


async def inicio(request: Request) -> Response:
    store, now = request.app.state.store, request.app.state.clock()
    pending = next((s.slug for s in STEPS if not s.optional and not s.done(store, now)), None)
    return RedirectResponse(f"/asistente/{pending}" if pending else "/", status_code=303)


async def ver(request: Request) -> Response:
    step = _step(request)
    if step is None:
        raise HTTPException(404)
    return _page(request, step, _input(request), saved=request.query_params.get("guardado") == "1")


async def guardar(request: Request) -> Response:
    step = _step(request)
    if step is None:
        raise HTTPException(404)
    try:
        form = await read_form(request)
    except FormTooLarge:
        return error_page(request, 413, "El formulario es demasiado grande.")
    except FormInvalid:
        return error_page(request, 400, "No se ha entendido el formulario.")
    htmx = request.headers.get("HX-Request") == "true"
    step_input = _input(request, form)
    try:
        step.save(step_input)
    except StepRejected as rejected:
        return _page(
            request,
            step,
            step_input,
            errors=rejected.errors,
            form_errors=rejected.form_errors or ["Revisa los campos marcados."],
            status_code=200 if htmx else 422,
        )
    written = step_input.store.written
    if written:
        audit_write(
            identity=step_input.email,
            step=step.slug,
            action=first(form, "accion"),
            keys=written,
            at=step_input.now,
        )
    if htmx:
        return _page(request, step, _input(request), saved=bool(written))
    suffix = "?guardado=1" if written else ""
    return RedirectResponse(f"/asistente/{step.slug}{suffix}", status_code=303)


def wizard_routes() -> list[BaseRoute]:
    return [
        Route("/asistente", inicio, methods=["GET"]),
        Route("/asistente/{slug}", ver, methods=["GET"]),
        Route("/asistente/{slug}", guardar, methods=["POST"]),
    ]
