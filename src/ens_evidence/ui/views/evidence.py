"""Evidencias: solo manifiestos, nunca el contenido de los artefactos. Solo lee."""

from starlette.requests import Request
from starlette.responses import Response

from ens_evidence.evidence import list_manifests
from ens_evidence.ui.pages import page_context, render
from ens_evidence.ui.views import format_instant


def evidencias(request: Request) -> Response:
    store, now = request.app.state.store, request.app.state.clock()
    listing = list_manifests(store)
    return render(
        request,
        "views/evidencias.html",
        **page_context(request, "evidencias", "Evidencias"),
        manifests=[
            {
                "id": m.id,
                "source": m.source,
                "collected_at": format_instant(m.collected_at),
                "status": "EXPIRED" if m.valid_until and m.valid_until <= now else "CURRENT",
            }
            for m in sorted(listing.manifests, key=lambda m: m.collected_at, reverse=True)
        ],
    )
