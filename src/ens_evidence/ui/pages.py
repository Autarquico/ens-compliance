"""Piezas de página comunes (identidad, plantilla, contexto, error) sin depender de la fábrica."""

from collections.abc import Mapping
from typing import Any

from starlette.requests import Request
from starlette.responses import HTMLResponse

from ens_evidence.ui.access import Identity

PRODUCT_NAME = "ens-compliance"


def identity_of(request: Request) -> Identity:
    return request.state.identity


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
