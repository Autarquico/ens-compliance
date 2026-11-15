"""Cliente de OpenBao con lista blanca de (método, ruta).

Solo hay consultas, salvo `renew-self`. Cualquier otra combinación se rechaza antes de enviar
nada. El token vive solo aquí: no aparece en `repr`, en excepciones ni en logs.
"""

import re
from dataclasses import dataclass
from typing import Any, Self
from urllib.parse import quote

import httpx

from ens_evidence.models.enums import CollectionStatus

TIMEOUT_SECONDS = 30.0
MESSAGE_LIMIT = 200
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_SEGMENT = r"(?!\.{1,2}(?:/|$))[A-Za-z0-9._~%-]+"  # nunca `.` ni `..`

ALLOWED: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (method, re.compile(pattern))
    for method, pattern in (
        ("GET", r"sys/seal-status"),
        ("GET", r"sys/audit"),
        ("GET", r"sys/mounts"),
        ("GET", r"sys/policies/acl/ens-app-metadata"),
        ("LIST", rf"kv/metadata/(?:{_SEGMENT}/)*"),
        ("GET", rf"kv/metadata/(?:{_SEGMENT}/)*{_SEGMENT}"),
        ("LIST", r"transit/keys/?"),
        ("GET", rf"transit/keys/{_SEGMENT}"),
        ("LIST", r"auth/token/accessors/?"),
        ("POST", r"auth/token/lookup-accessor"),
        ("GET", r"auth/token/lookup-self"),
        ("POST", r"auth/token/renew-self"),
        ("POST", r"sys/capabilities-self"),
    )
)


class KeystoreError(Exception):
    """Fallo saneado: el mensaje es apto para evidencias, logs y usuario."""

    def __init__(self, status: CollectionStatus, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class NotFound(KeystoreError):
    def __init__(self) -> None:
        super().__init__(CollectionStatus.ERROR, "HTTP 404: no existe")


class Denied(KeystoreError):
    def __init__(self) -> None:
        super().__init__(
            CollectionStatus.PERMISSION_DENIED,
            "HTTP 403: la app no tiene permiso para comprobarlo (o su token no es válido)",
        )


@dataclass(frozen=True)
class Reply:
    """`data` de la respuesta de OpenBao (vacío si no trae)."""

    data: dict[str, Any]
    body: dict[str, Any]


def segment(name: str) -> str:
    """Un nombre recibido de OpenBao como segmento de ruta; nunca `.` ni `..`."""
    if name in {"", ".", ".."}:
        raise KeystoreError(CollectionStatus.ERROR, "nombre no válido en la ruta")
    return quote(name, safe="")


class KeystoreClient:
    def __init__(
        self,
        address: str,
        token: str | None,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._token = token
        headers = {"Accept": "application/json"}
        if token:
            headers["X-Vault-Token"] = token
        self._http = httpx.Client(
            base_url=f"{address.rstrip('/')}/v1/",
            transport=transport,
            timeout=TIMEOUT_SECONDS,
            headers=headers,
            follow_redirects=False,
        )

    def __repr__(self) -> str:
        return "KeystoreClient()"

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._http.close()

    def get(self, path: str) -> Reply:
        return self._send("GET", path)

    def list(self, path: str) -> list[str]:
        keys = self._send("LIST", path).data.get("keys")
        if not isinstance(keys, list) or not all(isinstance(k, str) for k in keys):
            raise KeystoreError(CollectionStatus.ERROR, "listado sin `keys` válido")
        return keys

    def post(self, path: str, body: dict[str, Any]) -> Reply:
        return self._send("POST", path, body)

    def _send(self, method: str, path: str, body: dict[str, Any] | None = None) -> Reply:
        if not any(m == method and p.fullmatch(path) for m, p in ALLOWED):
            raise KeystoreError(CollectionStatus.ERROR, f"ruta no permitida: {method} {path}")
        try:
            response = self._http.request(method, path, json=body)
        except httpx.HTTPError as exc:
            raise KeystoreError(
                CollectionStatus.ERROR, f"fallo de red: {type(exc).__name__}"
            ) from None
        return self._unwrap(response)

    def _unwrap(self, response: httpx.Response) -> Reply:
        status = response.status_code
        if status == 403:
            raise Denied
        if status == 404:
            raise NotFound
        if not response.is_success:
            raise KeystoreError(CollectionStatus.ERROR, f"HTTP {status}: {self._detail(response)}")
        try:
            body = response.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise KeystoreError(CollectionStatus.ERROR, f"HTTP {status}: respuesta mal formada")
        data = body.get("data")
        return Reply(data if isinstance(data, dict) else {}, body)

    def _detail(self, response: httpx.Response) -> str:
        try:
            errors = response.json().get("errors")
        except (ValueError, AttributeError):
            errors = None
        text = "; ".join(str(e) for e in errors[:3]) if isinstance(errors, list) else ""
        return self._clean(text) or "sin detalle"

    def _clean(self, text: str) -> str:
        if self._token:
            text = text.replace(self._token, "[redactado]")
        return _CONTROL.sub(" ", text)[:MESSAGE_LIMIT]
