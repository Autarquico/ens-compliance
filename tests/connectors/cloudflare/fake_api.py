"""API de Cloudflare simulada con `httpx.MockTransport` a partir de `fixtures/*.json`."""

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

FIXTURES = Path(__file__).parent / "fixtures"
PREFIX = "/client/v4"
ACCOUNT = "0123456789abcdef0123456789abcdef"
ZONE = "zone-1"
TOKEN = "cf-token-CANARY-0123456789abcdef"
NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
TUNNEL_REMOTE = "11111111-1111-1111-1111-111111111111"
TUNNEL_LOCAL = "22222222-2222-2222-2222-222222222222"
SETTING_VALUES = {"ssl": "strict", "min_tls_version": "1.2", "always_use_https": "on"}

Reply = dict | tuple[int, Any] | list | Callable[[httpx.Request], httpx.Response]


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def envelope(result: Any, info: dict[str, Any] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"success": True, "errors": [], "messages": [], "result": result}
    if info is not None:
        body["result_info"] = info
    return body


def failure(code: int, message: str, **extra: Any) -> dict[str, Any]:
    return {
        "success": False,
        "errors": [{"code": code, "message": message, **extra}],
        "messages": [],
        "result": None,
    }


def denied() -> tuple[int, dict[str, Any]]:
    return 403, failure(10000, "Authentication error")


def _setting(request: httpx.Request) -> httpx.Response:
    name = request.url.path.rsplit("/", 1)[1]
    return httpx.Response(
        200, json=envelope({"id": name, "value": SETTING_VALUES[name], "editable": True})
    )


def _defaults() -> list[tuple[str, Reply]]:
    return [
        (rf"/(accounts/{ACCOUNT}|user)/tokens/verify", fixture("verify")),
        ("/zones", fixture("zones")),
        (rf"/zones/{ZONE}/dns_records", fixture("dns_records")),
        (rf"/zones/{ZONE}/settings/(ssl|min_tls_version|always_use_https)", _setting),
        (rf"/zones/{ZONE}/rulesets", fixture("rulesets")),
        (rf"/accounts/{ACCOUNT}/access/apps", fixture("access_apps")),
        (rf"/accounts/{ACCOUNT}/access/apps/app-1/policies", fixture("access_policies")),
        (rf"/accounts/{ACCOUNT}/cfd_tunnel", fixture("tunnels")),
        (
            rf"/accounts/{ACCOUNT}/cfd_tunnel/{TUNNEL_REMOTE}/configurations",
            fixture("tunnel_configuration"),
        ),
        (rf"/accounts/{ACCOUNT}/logs/audit", fixture("audit_logs")),
    ]


class FakeApi:
    """Responde por ruta: primero los reemplazos de cada test, luego los fixtures."""

    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []
        self._overrides: list[tuple[str, Reply]] = []
        self._defaults = _defaults()
        self.transport = httpx.MockTransport(self._handle)

    def set(self, pattern: str, reply: Reply) -> None:
        """`dict` -> 200 JSON; `(status, cuerpo)`; lista -> una respuesta por llamada (la última
        se repite); callable -> `Response`."""
        self._overrides.insert(0, (pattern, reply))

    def paths(self) -> list[str]:
        return [request.url.path.removeprefix(PREFIX) for request in self.calls]

    def params(self, pattern: str) -> list[dict[str, str]]:
        return [
            dict(request.url.params)
            for request in self.calls
            if re.fullmatch(pattern, request.url.path.removeprefix(PREFIX))
        ]

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        path = request.url.path.removeprefix(PREFIX)
        for table in (self._overrides, self._defaults):
            for pattern, reply in table:
                if re.fullmatch(pattern, path):
                    return self._respond(pattern, reply, request)
        return httpx.Response(404, json=failure(7003, "No route for that URI"))

    def _respond(self, pattern: str, reply: Reply, request: httpx.Request) -> httpx.Response:
        if isinstance(reply, list):
            seen = sum(
                1
                for earlier in self.calls[:-1]
                if re.fullmatch(pattern, earlier.url.path.removeprefix(PREFIX))
            )
            reply = reply[min(seen, len(reply) - 1)]
        if callable(reply):
            return reply(request)
        if isinstance(reply, tuple):
            status, body = reply
            if isinstance(body, bytes | str):
                return httpx.Response(status, content=body)
            return httpx.Response(status, json=body)
        return httpx.Response(200, json=reply)
