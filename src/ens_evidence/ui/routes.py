"""Rutas de la UI: asistente y vistas de solo lectura."""

from starlette.routing import BaseRoute, Route

from ens_evidence.ui.views.dossier import expediente
from ens_evidence.ui.views.evidence import evidencias
from ens_evidence.ui.views.matrix import matriz
from ens_evidence.ui.views.preaudit import preauditoria
from ens_evidence.ui.views.status import estado
from ens_evidence.ui.wizard import wizard_routes


def ui_routes() -> list[BaseRoute]:
    return [
        Route("/", estado, methods=["GET"]),
        Route("/matriz", matriz, methods=["GET"]),
        Route("/evidencias", evidencias, methods=["GET"]),
        Route("/expediente", expediente, methods=["GET"]),
        Route("/preauditoria", preauditoria, methods=["GET"]),
        *wizard_routes(),
    ]
