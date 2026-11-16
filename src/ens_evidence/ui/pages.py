"""Piezas de página comunes (identidad, plantilla, contexto, error) sin depender de la fábrica."""

from collections.abc import Mapping
from typing import Any

from starlette.requests import Request
from starlette.responses import HTMLResponse

from ens_evidence.ui.access import Identity
from ens_evidence.ui.mfa_notice import mfa_notices
from ens_evidence.ui.monitoring_banner import banner_for
from ens_evidence.ui.roles import Permissions, RolesStatus

PRODUCT_NAME = "ens-compliance"


def identity_of(request: Request) -> Identity:
    return request.state.identity


def permissions_of(request: Request) -> Permissions:
    """Los permisos de esta petición; sin ellos (no debería pasar), solo lectura."""
    return getattr(request.state, "permissions", None) or Permissions()


def roles_notices(permissions: Permissions) -> list[dict[str, str]]:
    """Avisos globales sobre quién puede editar; vacío si esta persona puede editar."""
    if permissions.edit:
        return []
    if permissions.status is RolesStatus.INVALID:
        title = "El fichero de roles no es válido, así que nadie puede editar."
        how = (
            "Quien administra la herramienta debe corregir `settings/ui-roles.yaml` "
            "o volver a crearlo con `ens-evidence ui roles grant <correo> --edit`."
        )
    elif permissions.status is RolesStatus.MISSING:
        title = "Todavía no hay roles asignados: la interfaz está en solo lectura."
        how = "Quien administra la herramienta puede asignarlos con `ens-evidence ui roles grant`."
    else:
        title = "Tu usuario solo puede consultar."
        how = "Para editar, pide `ens-evidence ui roles grant <tu correo> --edit`."
    return [{"kind": "warn", "title": title, "how": how}]


def render(request: Request, template: str, status_code: int = 200, **context: Any) -> HTMLResponse:
    body = request.app.state.templates.get_template(template).render(
        identity=identity_of(request), **context
    )
    return HTMLResponse(body, status_code=status_code)


def page_context(request: Request, nav_active: str, page_title: str) -> dict[str, Any]:
    return {
        "product_name": PRODUCT_NAME,
        "identity_email": identity_of(request).email,
        "nav_active": nav_active,
        "page_title": page_title,
        "monitoring_banner": banner_for(request),
        "permissions": permissions_of(request),
        "role_notices": [*mfa_notices(request), *roles_notices(permissions_of(request))],
    }


def error_page(
    request: Request,
    status: int,
    message: str | None = None,
    headers: Mapping[str, str] | None = None,
) -> HTMLResponse:
    response = render(
        request,
        "error.html",
        status_code=status,
        status=status,
        message=message,
        **page_context(request, "estado", "Error"),
    )
    response.headers.update(headers or {})
    return response
