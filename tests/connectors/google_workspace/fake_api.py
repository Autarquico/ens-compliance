"""Admin SDK y endpoint de token simulados con `httpx.MockTransport` y `fixtures/*.json`."""

import json
import re
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
ACCESS_TOKEN = "ya29.acceso-ZXCV1234asdf5678"
ADMIN_SUBJECT = "admin@taller-norte.example"
DOMAIN = "taller-norte.example"
API_HOST = "admin.googleapis.com"
TOKEN_HOST = "oauth2.googleapis.com"

DIRECTORY = "/admin/directory/v1"
CUSTOMER = f"{DIRECTORY}/customers/my_customer"
USERS = f"{DIRECTORY}/users"
ROLES = f"{DIRECTORY}/customer/my_customer/roles"
ASSIGNMENTS = f"{DIRECTORY}/customer/my_customer/roleassignments"
TOKEN_EVENTS = "/admin/reports/v1/activity/users/all/applications/token"
ADMIN_EVENTS = "/admin/reports/v1/activity/users/all/applications/admin"
TOKEN_PATH = "/token"

ANA, LUIS, MARTA, PACO, DELIA, INMA, VIEJA = (f"10000000000000000000{n}" for n in range(1, 8))

Reply = dict | list | tuple[int, Any] | Callable[[httpx.Request], httpx.Response]


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def user(user_id: str) -> dict[str, Any]:
    return deepcopy(next(u for u in fixture("users")["users"] if u["id"] == user_id))


def google_error(
    code: int, reason: str, text: str = "body-CANARY", *, status: str | None = None
) -> tuple[int, dict[str, Any]]:
    error: dict[str, Any] = {
        "code": code,
        "message": text,
        "errors": [{"message": text, "domain": "global", "reason": reason}],
    }
    if status:
        error["status"] = status
    return code, {"error": error}


def denied() -> tuple[int, dict[str, Any]]:
    return google_error(403, "insufficientPermissions", status="PERMISSION_DENIED")


def unauthorized() -> tuple[int, dict[str, Any]]:
    return google_error(401, "authError", status="UNAUTHENTICATED")


def token_error(error: str, description: str = "descripcion-CANARY") -> tuple[int, dict]:
    return 400, {"error": error, "error_description": description}


def token_response(
    token: str = ACCESS_TOKEN, expires_in: Any = 3600
) -> Callable[[httpx.Request], httpx.Response]:
    body = {"access_token": token, "expires_in": expires_in, "token_type": "Bearer"}
    return lambda request: httpx.Response(200, json=body)


def paginated(wrapper: str, *pages: list[Any]) -> Callable[[httpx.Request], httpx.Response]:
    """La página n se pide con `pageToken=page-n`; todas menos la última traen token."""

    def reply(request: httpx.Request) -> httpx.Response:
        token = request.url.params.get("pageToken")
        index = 0 if token is None else int(token.removeprefix("page-"))
        body: dict[str, Any] = {wrapper: pages[index]}
        if index + 1 < len(pages):
            body["nextPageToken"] = f"page-{index + 1}"
        return httpx.Response(200, json=body)

    return reply


def _assignments(request: httpx.Request) -> httpx.Response:
    items = fixture("roleassignments").get(request.url.params.get("userKey", ""), [])
    return httpx.Response(200, json={"items": items} if items else {})


def _defaults() -> list[tuple[str, Reply]]:
    return [
        (TOKEN_PATH, token_response()),
        (CUSTOMER, fixture("customer")),
        (USERS, fixture("users")),
        (ROLES, fixture("roles")),
        (ASSIGNMENTS, _assignments),
        (TOKEN_EVENTS, fixture("token_activity")),
        (ADMIN_EVENTS, fixture("admin_activity")),
    ]


class FakeApi:
    """Responde por ruta: primero los reemplazos de cada test, luego los fixtures."""

    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []
        self.assertions: list[str] = []
        self._overrides: list[tuple[str, Reply]] = []
        self._defaults = _defaults()
        self.transport = httpx.MockTransport(self._handle)

    def set(self, pattern: str, reply: Reply) -> None:
        """`dict`/`list` -> 200 JSON; `(status, cuerpo)`; `(status, cuerpo, cabeceras)`;
        callable -> `Response` (puede lanzar un error de red)."""
        self._overrides.insert(0, (pattern, reply))

    def sequence(self, pattern: str, replies: list[Reply]) -> None:
        """Una respuesta por llamada a la ruta; la última se repite."""
        counter = {"seen": 0}

        def reply(request: httpx.Request) -> httpx.Response:
            chosen = replies[min(counter["seen"], len(replies) - 1)]
            counter["seen"] += 1
            return self._build(chosen, request)

        self.set(pattern, reply)

    def paths(self) -> list[str]:
        return [request.url.path for request in self.calls]

    def hosts(self) -> set[str]:
        return {request.url.host for request in self.calls}

    def api_calls(self, pattern: str | None = None) -> list[httpx.Request]:
        return [
            r
            for r in self.calls
            if r.url.host == API_HOST and (pattern is None or re.fullmatch(pattern, r.url.path))
        ]

    def params(self, pattern: str) -> list[dict[str, str]]:
        return [dict(request.url.params) for request in self.api_calls(pattern)]

    def token_requests(self) -> list[httpx.Request]:
        return [r for r in self.calls if r.url.path == TOKEN_PATH]

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if request.url.path == TOKEN_PATH:
            self.assertions += parse_qs(request.content.decode()).get("assertion", [])
        elif (
            request.url.host == API_HOST
            and request.headers.get("Authorization") != f"Bearer {ACCESS_TOKEN}"
        ):
            return httpx.Response(401, json=unauthorized()[1])
        for table in (self._overrides, self._defaults):
            for pattern, reply in table:
                if re.fullmatch(pattern, request.url.path):
                    return self._build(reply, request)
        return httpx.Response(404, json=google_error(404, "notFound")[1])

    @staticmethod
    def _build(reply: Reply, request: httpx.Request) -> httpx.Response:
        if callable(reply):
            return reply(request)
        if isinstance(reply, tuple):
            status, body, *rest = reply
            headers = rest[0] if rest else None
            if isinstance(body, bytes | str):
                return httpx.Response(status, content=body, headers=headers)
            return httpx.Response(status, json=body, headers=headers)
        return httpx.Response(200, json=reply)
