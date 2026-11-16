"""Autorización de la UI: una política explícita por operación y el middleware que la aplica.

Una operación es (método, ruta registrada) y, cuando la ruta reparte en varias (`/asistente/{slug}`,
`/matriz/{medida}/{accion}`…), también el valor de ese parámetro. Lo que no está aquí se deniega
antes de escribir. Un test exige que la tabla coincida con las rutas, los pasos y las acciones.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Match, Route
from starlette.types import ASGIApp, Receive, Scope, Send

from ens_evidence.models.enums import Role
from ens_evidence.ui.access import HEALTH_PATH, Identity
from ens_evidence.ui.audit import audit_denied
from ens_evidence.ui.pages import error_page
from ens_evidence.ui.roles import ROLE_LABELS, Permissions, RoleDenied, load_roles
from ens_evidence.ui.security import SAFE_METHODS


class Need(StrEnum):
    EDIT = "edit"  # basta la edición
    CHOSEN = (
        "chosen"  # edición y el rol que la persona elige en el formulario; lo valida el handler
    )


Requirement = Need | Role  # un `Role` fijo exige además ese rol asignado


@dataclass(frozen=True)
class ByParam:
    """La ruta reparte por un parámetro del camino; cada valor tiene su requisito."""

    param: str
    policies: Mapping[str, Requirement]


Operations = Mapping[tuple[str, str], Requirement | ByParam]

EDIT, CHOSEN = Need.EDIT, Need.CHOSEN
SO, SYSTEM_OWNER, MANAGEMENT = Role.SECURITY_OFFICER, Role.SYSTEM_OWNER, Role.MANAGEMENT

WIZARD_STEPS: dict[str, Requirement] = {
    "sistema": EDIT,
    "activos": EDIT,
    "servicios": EDIT,
    "dependencias": EDIT,
    "aprobacion-alcance": SYSTEM_OWNER,
    "informacion": EDIT,
    "valoraciones": EDIT,  # los borradores con edición; cada firma, el rol de su elemento (handler)
    "aprobacion-categorizacion": SO,
    "riesgos-metodologia": EDIT,
    "riesgos": EDIT,
    "aprobacion-riesgos": SO,
    "aplicabilidad": EDIT,
    "aprobacion-aplicabilidad": SO,
    "documentos-organizacion": EDIT,
    "documentos-responsables": EDIT,
    "documentos-normativa": EDIT,
    "documentos-procedimientos": EDIT,
    "documentos-informacion": EDIT,
    "documentos-incidentes": EDIT,
    "documentos-autorizaciones": EDIT,
    "documentos-generar": EDIT,
    "documentos-aprobar": EDIT,  # el rol sale de la ficha del documento (handler)
}
MATRIX_ACTIONS: dict[str, Requirement] = {
    "responsable": EDIT,
    "enlazar": EDIT,
    "desenlazar": EDIT,
    "revisar": CHOSEN,
}
DOSSIER_ACTIONS: dict[str, Requirement] = {
    "self-assess": CHOSEN,
    "analyze": SO,
    "declare": MANAGEMENT,
}
INCIDENT_ACTIONS: dict[str, Requirement] = {"actualizar": EDIT, "cerrar": EDIT, "historial": EDIT}

WIZARD_PATH = "/asistente/{slug}"
DOSSIER_PATH = "/expediente/{dossier}/{accion}"
OPERATIONS: Operations = {
    ("POST", WIZARD_PATH): ByParam("slug", WIZARD_STEPS),
    ("POST", "/matriz/iniciar"): EDIT,
    ("POST", "/matriz/{medida}/{accion}"): ByParam("accion", MATRIX_ACTIONS),
    ("POST", "/evidencias/nueva"): EDIT,
    ("POST", "/expediente/generar"): EDIT,
    ("POST", DOSSIER_PATH): ByParam("accion", DOSSIER_ACTIONS),
    ("POST", "/incidentes/nuevo"): EDIT,
    ("POST", "/incidentes/{incidente}/{accion}"): ByParam("accion", INCIDENT_ACTIONS),
    ("POST", "/preauditoria/preparar"): EDIT,
    ("POST", "/preauditoria/enviar"): SO,
    ("POST", "/preauditoria/hallazgos/{finding}"): SO,
    ("POST", "/herramientas/claves/custodios"): CHOSEN,
    ("POST", "/herramientas/copias"): EDIT,
    ("POST", "/herramientas/claves"): EDIT,
    ("POST", "/herramientas/contrasenas"): EDIT,
}


def fixed_role(path: str, key: str) -> Role:
    """El rol fijo de una acción repartida; para que el handler use el mismo que autoriza."""
    entry = OPERATIONS[("POST", path)]
    assert isinstance(entry, ByParam)
    role = entry.policies[key]
    assert isinstance(role, Role)
    return role


@dataclass(frozen=True)
class Denial:
    reason: str
    role: Role | None = None


def _matched_route(scope: Scope) -> tuple[Route, Scope] | None:
    for route in scope["app"].router.routes:
        if isinstance(route, Route):
            match, child = route.matches(scope)
            if match is Match.FULL:
                return route, child
    return None


def _requirement(
    operations: Operations, method: str, route: Route, child: Scope
) -> Requirement | None:
    """El requisito de la operación; `None` si no está en la tabla."""
    entry = operations.get((method, route.path))
    if isinstance(entry, ByParam):
        return entry.policies.get(child["path_params"].get(entry.param, ""))
    return entry


def decide(permissions: Permissions, requirement: Requirement | None) -> Denial | None:
    if requirement is None:
        return Denial("unknown_operation")
    if not permissions.edit:
        return Denial("no_edit")
    if isinstance(requirement, Role):
        try:
            permissions.require(requirement)
        except RoleDenied:
            return Denial("missing_role", requirement)
    return None


def denial_message(denial: Denial) -> str:
    if denial.reason == "no_edit":
        return (
            "Tu usuario puede consultar, pero no editar. Quien administra la herramienta puede "
            "darte permiso de edición con `ens-evidence ui roles grant <tu correo> --edit`. "
            "No se ha guardado nada."
        )
    if denial.reason == "missing_role" and denial.role is not None:
        return (
            f"Esto lo hace quien tiene el rol «{ROLE_LABELS[denial.role]}» y no lo tienes "
            f"asignado. Quien administra la herramienta puede asignártelo con "
            f"`ens-evidence ui roles grant <tu correo> --role {denial.role.value}`. "
            "No se ha guardado nada."
        )
    return "Esta acción queda fuera de lo que la interfaz permite. No se ha guardado nada."


def denied_response(request: Request, denial: Denial, *, method: str, path: str) -> Response:
    """403 con la página explicativa (también para htmx) y la línea de auditoría `denied`."""
    audit_denied(
        identity=request.state.identity.email,
        method=method,
        path=path,
        reason=denial.reason,
        role=denial.role.value if denial.role else None,
        at=request.app.state.clock(),
    )
    return error_page(request, 403, denial_message(denial))


async def role_denied_handler(request: Request, exc: Exception) -> Response:
    """Un handler pidió un rol que el usuario no tiene (rol elegido en el formulario…)."""
    assert isinstance(exc, RoleDenied)
    return denied_response(
        request,
        Denial("missing_role", exc.role),
        method=request.method,
        path=request.url.path,
    )


class AuthorizationMiddleware:
    """Pone los permisos de la petición y deniega, sin escribir, lo que la política no admite."""

    def __init__(self, app: ASGIApp, *, operations: Operations = OPERATIONS) -> None:
        self._app = app
        self._operations = operations

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        identity: Identity | None = scope.get("state", {}).get("identity")
        if scope["type"] != "http" or scope["path"] == HEALTH_PATH or identity is None:
            await self._app(scope, receive, send)
            return
        roles = load_roles(scope["app"].state.store)
        permissions = Permissions.of(roles, identity.email)
        scope["state"]["permissions"] = permissions
        matched = None if scope["method"] in SAFE_METHODS else _matched_route(scope)
        if matched is not None:  # sin ruta, el router responde 404/405 y no se escribe nada
            requirement = _requirement(self._operations, scope["method"], *matched)
            denial = decide(permissions, requirement)
            if denial is not None:
                request = Request(scope, receive)
                response = denied_response(
                    request, denial, method=scope["method"], path=scope["path"]
                )
                await response(scope, receive, send)
                return
        await self._app(scope, receive, send)
