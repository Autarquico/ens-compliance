"""OpenBao simulado con `httpx.MockTransport`, con registro de peticiones."""

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from importlib import resources
from typing import Any

import httpx

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
TOKEN = "hvs.TOKEN-CANARIO-NO-LOGS"
ACCESSOR_ROOT, ACCESSOR_APP = "acc-root-canario", "acc-app-canario"
CUSTOM = "custom-metadata-canario"
PUBLIC_KEY = "-----BEGIN PUBLIC KEY-----CANARIO"
META = "meta-canario"
LEAKED_ID = "id-canario"
POLICY = "ens-app-metadata"


def days_ago(days: float) -> str:
    return (NOW - timedelta(days=days)).isoformat().replace("+00:00", "Z")


def epoch_ago(days: float) -> int:
    return int((NOW - timedelta(days=days)).timestamp())


Reply = tuple[int, dict[str, Any]] | Callable[[httpx.Request], httpx.Response]

SEAL_OPEN = {
    "initialized": True,
    "sealed": False,
    "t": 2,
    "n": 3,
    "progress": 0,
    "version": "2.7.2",
}
SEAL_CLOSED = {**SEAL_OPEN, "sealed": True}
APP_LOOKUP = {
    "policies": [POLICY],
    "period": 72 * 3600,
    "ttl": 70 * 3600,
    "renewable": True,
    "orphan": True,
    "id": LEAKED_ID,
    "meta": {"x": META},
}


def kv_entry(
    created: str, version: int = 1, *, destroyed: bool = False, deletion_time: str = ""
) -> dict[str, Any]:
    return {
        "data": {
            "current_version": version,
            "oldest_version": 1,
            "custom_metadata": {"nota": CUSTOM},
            "versions": {
                str(version): {
                    "created_time": created,
                    "deletion_time": deletion_time,
                    "destroyed": destroyed,
                }
            },
        }
    }


def defaults() -> dict[tuple[str, str], Reply]:
    deny = {"data": {"capabilities": ["deny"]}}
    return {
        ("GET", "sys/seal-status"): (200, SEAL_OPEN),
        ("GET", "sys/audit"): (200, {"data": {"archivo/": {"type": "file", "options": {}}}}),
        ("GET", "sys/mounts"): (
            200,
            {
                "data": {
                    "kv/": {"type": "kv", "options": {"version": "2"}},
                    "transit/": {"type": "transit", "options": {}},
                    "sys/": {"type": "system", "options": None},
                    "cubbyhole/": {"type": "cubbyhole", "options": None},
                    "identity/": {"type": "identity", "options": None},
                }
            },
        ),
        ("GET", f"sys/policies/acl/{POLICY}"): (
            200,
            {"data": {"name": POLICY, "policy": _policy_text()}},
        ),
        ("LIST", "kv/metadata/"): (200, {"data": {"keys": ["app", "dir/"]}}),
        ("LIST", "kv/metadata/dir/"): (200, {"data": {"keys": ["db"]}}),
        ("GET", "kv/metadata/app"): (200, kv_entry(days_ago(10))),
        ("GET", "kv/metadata/dir/db"): (200, kv_entry(days_ago(20), 3)),
        ("LIST", "transit/keys/"): (200, {"data": {"keys": ["firma", "sim"]}}),
        ("GET", "transit/keys/sim"): (
            200,
            {
                "data": {
                    "type": "aes256-gcm96",
                    "latest_version": 2,
                    "keys": {"1": epoch_ago(100), "2": epoch_ago(5)},
                    "auto_rotate_period": 0,
                    "exportable": False,
                    "allow_plaintext_backup": False,
                }
            },
        ),
        ("GET", "transit/keys/firma"): (
            200,
            {
                "data": {
                    "type": "ed25519",
                    "latest_version": 1,
                    "keys": {"1": {"creation_time": days_ago(3), "public_key": PUBLIC_KEY}},
                    "auto_rotate_period": 86400,
                }
            },
        ),
        ("LIST", "auth/token/accessors"): (
            200,
            {"data": {"keys": [ACCESSOR_APP, "acc-otro"]}},
        ),
        ("POST", "auth/token/lookup-accessor"): _lookup_accessor,
        ("GET", "auth/token/lookup-self"): (200, {"data": APP_LOOKUP}),
        ("POST", "sys/capabilities-self"): (200, deny),
    }


def _policy_text() -> str:
    source = resources.files("ens_evidence.kit") / "openbao" / "ens-app-metadata.hcl"
    return source.read_text()


def _lookup_accessor(request: httpx.Request) -> httpx.Response:
    accessor = json.loads(request.content)["accessor"]
    policies = ["root"] if accessor == ACCESSOR_ROOT else [POLICY]
    data = {"policies": policies, "orphan": True, "ttl": 100, "id": "", "meta": {"x": META}}
    return httpx.Response(200, json={"data": data})


class FakeOpenBao:
    """Responde por (método, ruta); lo no definido es 404. `requests` guarda (método, ruta)."""

    def __init__(self) -> None:
        self.replies = defaults()
        self.requests: list[tuple[str, str]] = []
        self.bodies: list[dict[str, Any]] = []
        self.tokens: list[str | None] = []
        self.transport = httpx.MockTransport(self._handle)

    def set(self, method: str, path: str, reply: Reply) -> None:
        self.replies[(method, path)] = reply

    def drop(self, method: str, path: str) -> None:
        self.replies.pop((method, path), None)

    def count(self, method: str, path: str) -> int:
        return self.requests.count((method, path))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.raw_path.decode().split("?")[0].removeprefix("/v1/")
        key = (request.method, path)
        self.requests.append(key)
        self.tokens.append(request.headers.get("x-vault-token"))
        if request.content:
            self.bodies.append(json.loads(request.content))
        reply = self.replies.get(key)
        if reply is None:
            return httpx.Response(404, json={"errors": []})
        if callable(reply):
            return reply(request)
        status, body = reply
        return httpx.Response(status, json=body)
