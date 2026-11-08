"""Vistas de solo lectura y utilidades comunes de las páginas."""

from datetime import UTC, datetime
from typing import Any

from starlette.requests import Request
from starlette.responses import HTMLResponse

from ens_evidence.ui.app import identity_of

PRODUCT_NAME = "ens-compliance"
NO_DATE = "—"


def format_instant(value: datetime | None) -> str:
    if value is None:
        return NO_DATE
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


def page_context(request: Request, nav_active: str, page_title: str) -> dict[str, Any]:
    return {
        "product_name": PRODUCT_NAME,
        "identity_email": identity_of(request).email,
        "nav_active": nav_active,
        "page_title": page_title,
    }


def error_page(request: Request, status: int, message: str | None = None) -> HTMLResponse:
    """`error.html` usa `status_code` como variable, que `render` reserva para el HTTP."""
    body = request.app.state.templates.get_template("error.html").render(
        identity=identity_of(request),
        status_code=status,
        message=message,
        **page_context(request, "estado", "Error"),
    )
    return HTMLResponse(body, status_code=status)
