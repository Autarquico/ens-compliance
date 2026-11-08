"""Fábrica de la app de la UI sobre la base HTTP común (ADR 0020, 0021)."""

import logging
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import jinja2
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import HTMLResponse, Response
from starlette.routing import BaseRoute, Mount
from starlette.staticfiles import StaticFiles

from ens_evidence.http.app import create_app
from ens_evidence.ui.access import AccessMiddleware, AccessVerifier, Identity, JwksLoader
from ens_evidence.ui.config import UiConfig
from ens_evidence.ui.security import (
    SECURITY_HEADERS,
    OriginCheckMiddleware,
    SecurityHeadersMiddleware,
)
from ens_evidence.ui.store import UiStore
from ens_evidence.workspace.store import WorkspaceStore

_LOGGER = logging.getLogger("ens_evidence.ui")

UI_DIR = Path(__file__).parent
TEMPLATES_DIR = UI_DIR / "templates"
STATIC_DIR = UI_DIR / "static"


def create_ui_app(
    config: UiConfig,
    store: WorkspaceStore,
    clock: Callable[[], datetime],
    jwks_loader: JwksLoader,
    routes: Sequence[BaseRoute] = (),
) -> Starlette:
    app = create_app(
        [*routes, Mount("/static", StaticFiles(directory=STATIC_DIR, check_dir=False))]
    )
    app.state.config = config
    app.state.store = UiStore(store)
    app.state.clock = clock
    app.state.templates = jinja2.Environment(
        loader=jinja2.FileSystemLoader(TEMPLATES_DIR), autoescape=True
    )
    verifier = AccessVerifier(
        issuer=config.issuer, audience=config.access_aud, jwks_loader=jwks_loader, clock=clock
    )
    app.add_exception_handler(HTTPException, _http_error_page)
    app.add_exception_handler(Exception, _unexpected_error_page)
    app.add_middleware(OriginCheckMiddleware, origin=config.origin)
    app.add_middleware(AccessMiddleware, verifier=verifier)
    app.add_middleware(SecurityHeadersMiddleware)
    return app


async def _http_error_page(request: Request, exc: Exception) -> Response:
    from ens_evidence.ui.views import error_page

    assert isinstance(exc, HTTPException)
    return error_page(request, exc.status_code, headers=exc.headers)


async def _unexpected_error_page(request: Request, exc: Exception) -> Response:
    """`ServerErrorMiddleware` queda fuera de los middlewares: las cabeceras van en la respuesta."""
    from ens_evidence.ui.views import error_page

    _LOGGER.error("outcome=error type=%s", type(exc).__name__)
    if hasattr(request.state, "identity"):
        response: Response = error_page(request, 500)
    else:
        response = HTMLResponse("Error interno", status_code=500)
    response.headers.update(SECURITY_HEADERS)
    return response


def identity_of(request: Request) -> Identity:
    return request.state.identity


def render(request: Request, template: str, status_code: int = 200, **context: Any) -> HTMLResponse:
    body = request.app.state.templates.get_template(template).render(
        identity=identity_of(request), **context
    )
    return HTMLResponse(body, status_code=status_code)
