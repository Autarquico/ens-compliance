"""Estado general: preparación, alcance, categorización y lo que falta. Solo lee."""

from starlette.requests import Request
from starlette.responses import Response

from ens_evidence.categorization import CategorizationState, categorization_status
from ens_evidence.checks.source import StoredCheckResults
from ens_evidence.matrix import matrix_status
from ens_evidence.matrix.messages import READINESS_LABELS, blocker_text
from ens_evidence.models.enums import Readiness
from ens_evidence.scope import ScopeState, scope_status
from ens_evidence.ui.app import render
from ens_evidence.ui.views import format_instant, page_context

READINESS_NOTICE = (
    "La herramienta no declara conformidad; la valoración es de la persona que revisa."
)


def next_step(scope: ScopeState, categorization: CategorizationState) -> tuple[str | None, str]:
    if scope is not ScopeState.APPROVED:
        return "/asistente/sistema", "Completar el alcance"
    if categorization is not CategorizationState.CURRENT:
        return "/asistente/informacion", "Completar la categorización"
    return None, ""


def estado(request: Request) -> Response:
    store, now = request.app.state.store, request.app.state.clock()
    result, _ = matrix_status(store, now, checks=StoredCheckResults(reference_now=now))
    scope = scope_status(store)
    categorization = categorization_status(store, now)
    readiness = result.readiness
    count = len(readiness.blockers)
    summary = f"Preparación: {READINESS_LABELS[readiness.readiness]}"
    if readiness.readiness is Readiness.NOT_READY:
        summary += f", {count} bloqueo" + ("" if count == 1 else "s")
    url, label = next_step(scope.state, categorization.state)
    category = categorization.computation.category if categorization.computation else None
    return render(
        request,
        "views/estado.html",
        **page_context(request, "estado", "Estado"),
        readiness={
            "state": readiness.readiness.value,
            "summary": f"{summary}. {READINESS_NOTICE}",
        },
        scope_state=scope.state.value,
        categorization_state=categorization.state.value,
        category=category.value if category else None,
        next_step_url=url,
        next_step_label=label,
        blockers=[
            {"kind": b.kind.value, "measure_id": b.measure_id, "text": blocker_text(b)}
            for b in readiness.blockers
        ],
        generated_at=format_instant(now),
    )
