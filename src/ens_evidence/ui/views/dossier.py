"""Expediente más reciente con el estado de su cadena y sus secciones. Solo lee."""

from starlette.requests import Request
from starlette.responses import Response

from ens_evidence.assessment.dossier import scan_transitions
from ens_evidence.assessment.dossier.store import list_dossiers
from ens_evidence.models.enums import DossierState
from ens_evidence.ui.app import render
from ens_evidence.ui.views import NO_DATE, format_instant, page_context

NOT_GENERATED = "Sin generar"
BROKEN = "ROTO"


def expediente(request: Request) -> Response:
    store = request.app.state.store
    readable = [d for d in list_dossiers(store) if d.snapshot is not None]
    context: dict = {
        "dossier_state": NOT_GENERATED,
        "generated_at": NO_DATE,
        "declared": None,
        "sections": [],
    }
    if readable:
        latest = readable[-1]
        scan = scan_transitions(store, latest.id)
        snapshot = latest.snapshot
        declared = None
        if scan.dossier is not None:
            declared = next(
                (t for t in scan.dossier.transitions if t.to_state is DossierState.DECLARED), None
            )
        generated = format_instant(snapshot.inputs.generated_at)
        context = {
            "dossier_state": scan.dossier.state.value if scan.dossier else BROKEN,
            "generated_at": generated,
            "declared": {"by": declared.actor, "at": format_instant(declared.at)}
            if declared
            else None,
            "sections": [
                {
                    "title": f"{s.number}. {s.title}",
                    "file": s.sources[0].key if s.sources else "—",
                    "state": s.state.value,
                    "generated_at": generated,
                    "fingerprint": s.sources[0].sha256 if s.sources else None,
                }
                for s in snapshot.sections
            ],
        }
    return render(
        request,
        "views/expediente.html",
        **page_context(request, "expediente", "Expediente"),
        **context,
    )
