"""Matriz de medidas: una fila por medida de la Declaración vigente. Solo lee."""

from functools import cache

from starlette.requests import Request
from starlette.responses import Response

from ens_evidence.catalog import load_catalog
from ens_evidence.categorization import CategorizationState, categorization_status
from ens_evidence.checks.source import StoredCheckResults
from ens_evidence.matrix import matrix_status
from ens_evidence.scope import ScopeState, scope_status
from ens_evidence.ui.pages import page_context, render
from ens_evidence.ui.views import format_instant


@cache
def measure_names() -> dict[str, str]:
    return {card.id: card.name for card in load_catalog().measures}


def matriz(request: Request) -> Response:
    store, now = request.app.state.store, request.app.state.clock()
    _, rows = matrix_status(store, now, checks=StoredCheckResults(reference_now=now))
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
            for r in rows
        ],
        wizard_pending=wizard_pending,
        generated_at=format_instant(now),
    )
