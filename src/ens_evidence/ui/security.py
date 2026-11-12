"""Cabeceras de seguridad y comprobación de origen en los POST (sin cookies, ADR 0021)."""

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

CSP = "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
SECURITY_HEADERS = {
    "content-security-policy": CSP,
    "referrer-policy": "no-referrer",
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
}
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [
                    (k, v)
                    for k, v in message["headers"]
                    if k.decode("latin-1").lower() not in SECURITY_HEADERS
                ]
                headers += [(k.encode(), v.encode()) for k, v in SECURITY_HEADERS.items()]
                message = {**message, "headers": headers}
            await send(message)

        await self._app(scope, receive, send_with_headers)


class OriginCheckMiddleware:
    """Un método que escribe exige `Origin` igual al de la UI y `Sec-Fetch-Site: same-origin`."""

    def __init__(self, app: ASGIApp, *, origin: str) -> None:
        self._app = app
        self._origin = origin

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] not in SAFE_METHODS:
            headers = {
                k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]
            }
            if (
                headers.get("origin") != self._origin
                or headers.get("sec-fetch-site") != "same-origin"
            ):
                response = JSONResponse({"error": "forbidden"}, status_code=403)
                await response(scope, receive, send)
                return
        await self._app(scope, receive, send)
