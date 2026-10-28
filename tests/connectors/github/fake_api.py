"""API REST de GitHub simulada con `httpx.MockTransport` a partir de `fixtures/*.json`."""

import json
import re
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

FIXTURES = Path(__file__).parent / "fixtures"
ORG = "taller-norte"
REPO = rf"/repos/{ORG}/[^/]+"
FACTURAS = rf"/repos/{ORG}/facturas"
TOKEN = "github_pat_TOKEN-0123456789abcdef"
NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
API = "https://api.github.com"

Reply = dict | list | tuple[int, Any] | Callable[[httpx.Request], httpx.Response]


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def variant(name: str, **changes: Any) -> Any:
    """Copia de un fixture con campos reemplazados; `None` como valor elimina la clave."""
    body = deepcopy(fixture(name))
    for key, value in changes.items():
        if value is None:
            body.pop(key, None)
        else:
            body[key] = value
    return body


def repo_body(name: str = "facturas", /, **changes: Any) -> dict[str, Any]:
    """Un repositorio del fixture con campos reemplazados; `None` elimina la clave."""
    body = deepcopy(next(repo for repo in fixture("repos") if repo["name"] == name))
    for key, value in changes.items():
        if value is None:
            body.pop(key, None)
        else:
            body[key] = value
    return body


def message(text: str, **extra: Any) -> dict[str, Any]:
    return {"message": text, "documentation_url": "https://docs.github.com/rest", **extra}


def denied() -> tuple[int, dict[str, Any]]:
    return 403, message("Resource not accessible by personal access token")


def not_found() -> tuple[int, dict[str, Any]]:
    return 404, message("Not Found")


def page(
    items: Any, *, next_url: str | None = None, status: int = 200, headers: dict | None = None
) -> Callable[[httpx.Request], httpx.Response]:
    """Respuesta con cabecera `Link: <next_url>; rel="next"`."""
    link = {"Link": f'<{next_url}>; rel="next"'} if next_url else {}
    return lambda request: httpx.Response(status, json=items, headers={**link, **(headers or {})})


def _defaults() -> list[tuple[str, Reply]]:
    return [
        ("/user", fixture("user")),
        (rf"/orgs/{ORG}", fixture("org")),
        (rf"/orgs/{ORG}/members", fixture("members_2fa_disabled")),
        (rf"/orgs/{ORG}/installations", fixture("installations")),
        (rf"/orgs/{ORG}/repos", fixture("repos")),
        (rf"{REPO}/rulesets", fixture("rulesets")),
        (rf"{REPO}/rulesets/\d+", fixture("ruleset_detail")),
        (rf"{REPO}/rules/branches/[^/]+", fixture("effective_rules")),
        (rf"{REPO}/branches/[^/]+/protection", fixture("protection")),
        (rf"{REPO}/vulnerability-alerts", (204, b"")),
        (rf"{REPO}/dependabot/alerts", (200, [])),
        (rf"{REPO}/collaborators", fixture("collaborators")),
        (rf"{REPO}/environments", fixture("environments")),
        (rf"{REPO}/actions/permissions", fixture("actions_permissions")),
        (rf"{REPO}/actions/permissions/workflow", fixture("workflow_permissions")),
        (rf"{REPO}/keys", fixture("deploy_keys")),
    ]


class FakeApi:
    """Responde por ruta: primero los reemplazos de cada test, luego los fixtures."""

    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []
        self._overrides: list[tuple[str, Reply]] = []
        self._defaults = _defaults()
        self.transport = httpx.MockTransport(self._handle)

    def set(self, pattern: str, reply: Reply) -> None:
        """`dict`/`list` -> 200 JSON; `(status, cuerpo)`; `(status, cuerpo, cabeceras)`;
        lista de tuplas no: usa `sequence`; callable -> `Response`."""
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

    def params(self, pattern: str) -> list[dict[str, str]]:
        return [
            dict(request.url.params)
            for request in self.calls
            if re.fullmatch(pattern, request.url.path)
        ]

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        for table in (self._overrides, self._defaults):
            for pattern, reply in table:
                if re.fullmatch(pattern, request.url.path):
                    return self._build(reply, request)
        return httpx.Response(404, json=message("Not Found"))

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
