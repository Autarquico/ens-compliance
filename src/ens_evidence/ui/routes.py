"""Rutas de la UI: asistente y vistas de solo lectura."""

from starlette.routing import BaseRoute

from ens_evidence.ui.views.dossier import DOSSIER_ROUTES
from ens_evidence.ui.views.evidence import EVIDENCE_ROUTES
from ens_evidence.ui.views.matrix import MATRIX_ROUTES
from ens_evidence.ui.views.preaudit import PREAUDIT_ROUTES
from ens_evidence.ui.views.status import STATUS_ROUTES
from ens_evidence.ui.wizard import wizard_routes


def ui_routes() -> list[BaseRoute]:
    return [
        *STATUS_ROUTES,
        *MATRIX_ROUTES,
        *EVIDENCE_ROUTES,
        *DOSSIER_ROUTES,
        *PREAUDIT_ROUTES,
        *wizard_routes(),
    ]
