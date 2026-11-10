"""Rutas de la UI: asistente y vistas de solo lectura."""

from starlette.routing import BaseRoute

from ens_evidence.ui.views.dossier import DOSSIER_ROUTES
from ens_evidence.ui.views.evidence import EVIDENCE_ROUTES
from ens_evidence.ui.views.help import HELP_ROUTES
from ens_evidence.ui.views.incidents import INCIDENT_ROUTES
from ens_evidence.ui.views.matrix import MATRIX_ROUTES
from ens_evidence.ui.views.preaudit import PREAUDIT_ROUTES
from ens_evidence.ui.views.status import STATUS_ROUTES
from ens_evidence.ui.views.tools import TOOLS_ROUTES
from ens_evidence.ui.views.tools_keystore import KEYSTORE_ROUTES
from ens_evidence.ui.wizard import wizard_routes


def ui_routes() -> list[BaseRoute]:
    return [
        *STATUS_ROUTES,
        *MATRIX_ROUTES,
        *EVIDENCE_ROUTES,
        *DOSSIER_ROUTES,
        *INCIDENT_ROUTES,
        *PREAUDIT_ROUTES,
        *TOOLS_ROUTES,
        *KEYSTORE_ROUTES,
        *HELP_ROUTES,
        *wizard_routes(),
    ]
