"""Fábrica de la aplicación Starlette; web-ui monta aquí sus routers."""

import logging
from collections.abc import Sequence

from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import BaseRoute, Route

from ens_evidence.http.limits import Rejected

_LOGGER = logging.getLogger("ens_evidence.http")


def _error(status: int, code: str, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse({"error": code}, status_code=status, headers=headers)


async def _rejected(_request: Request, exc: Exception) -> Response:
    assert isinstance(exc, Rejected)
    return _error(exc.status, exc.code)


async def _http_error(_request: Request, exc: Exception) -> Response:
    assert isinstance(exc, HTTPException)
    codes = {404: "not_found", 405: "method_not_allowed"}
    return _error(exc.status_code, codes.get(exc.status_code, "error"), dict(exc.headers or {}))


async def _unexpected(_request: Request, exc: Exception) -> Response:
    _LOGGER.error("outcome=error type=%s", type(exc).__name__)
    return _error(500, "internal_error")


async def _healthz(_request: Request) -> Response:
    return JSONResponse({"status": "ok"})


def create_app(routes: Sequence[BaseRoute] = ()) -> Starlette:
    return Starlette(
        debug=False,
        routes=[Route("/healthz", _healthz, methods=["GET"]), *routes],
        exception_handlers={
            Rejected: _rejected,
            HTTPException: _http_error,
            Exception: _unexpected,
        },
    )
