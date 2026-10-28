"""Cliente de solo lectura de la API de Cloudflare.

El token vive solo aquí: no aparece en `repr`, en excepciones ni en logs, y los
mensajes de error se reducen a código HTTP, códigos de Cloudflare y su mensaje truncado.
"""

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Self

import httpx

from ens_evidence.models.enums import CollectionStatus

API_BASE = "https://api.cloudflare.com/client/v4"
DEFAULT_PER_PAGE = 50
MAX_PAGES = 200
TIMEOUT_SECONDS = 30.0
MESSAGE_LIMIT = 200
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_PATH_SEGMENT = re.compile(r"[A-Za-z0-9_-]+")


class CloudflareError(Exception):
    """Fallo saneado: el mensaje es apto para evidencias, logs y usuario."""

    def __init__(self, status: CollectionStatus, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class CredentialRejected(CloudflareError):
    """El token no es válido, ha caducado o ha sido revocado."""

    def __init__(self, message: str) -> None:
        super().__init__(CollectionStatus.ERROR, message)


@dataclass(frozen=True)
class Reply:
    result: Any
    info: dict[str, Any] = field(default_factory=dict)


class Pager(ABC):
    """Adaptador de paginación de un endpoint."""

    def first(self) -> dict[str, Any]:
        return {}

    @abstractmethod
    def next(self, params: dict[str, Any], reply: Reply) -> dict[str, Any] | None: ...


class PageNumber(Pager):
    """`page`/`per_page`; fin por `total_pages`, si no `total_count`, si no página vacía."""

    def __init__(self, per_page: int = DEFAULT_PER_PAGE) -> None:
        self._per_page = per_page

    def first(self) -> dict[str, Any]:
        return {"page": 1, "per_page": self._per_page}

    def next(self, params: dict[str, Any], reply: Reply) -> dict[str, Any] | None:
        total = self._total_pages(params, reply.info)
        if total is None:
            following = bool(reply.result)
        elif not reply.result and params["page"] < total:
            raise CloudflareError(CollectionStatus.ERROR, "página vacía antes del total declarado")
        else:
            following = params["page"] < total
        return {**params, "page": params["page"] + 1} if following else None

    @staticmethod
    def _total_pages(params: dict[str, Any], info: dict[str, Any]) -> int | None:
        if "total_pages" in info:
            return _count(info["total_pages"], "total_pages")
        if "total_count" in info:
            count = _count(info["total_count"], "total_count")
            effective = info.get("per_page", params["per_page"])
            size = _count(effective, "per_page")
            if size < 1:
                raise CloudflareError(CollectionStatus.ERROR, "paginación con per_page no válido")
            return -(-count // size)
        return None


def _count(value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise CloudflareError(CollectionStatus.ERROR, f"paginación sin {name} válido")
    return value


# Máximos de `per_page` por endpoint según la doc de Cloudflare: /zones 50;
# /accounts/{id}/tunnels y .../access/apps/{id}/policies 1000. El resto usa el valor seguro.
ZONES_PAGER = PageNumber(50)
DNS_PAGER = PageNumber(1000)
TUNNELS_PAGER = PageNumber(1000)
POLICIES_PAGER = PageNumber(1000)


class CursorAfter(Pager):
    """Cursor opaco en `result_info.<path>` reenviado en el parámetro `cursor`."""

    def __init__(self, *path: str, param: str = "cursor") -> None:
        self._path = path
        self._param = param

    def next(self, params: dict[str, Any], reply: Reply) -> dict[str, Any] | None:
        cursor: Any = reply.info
        for step in self._path:
            cursor = cursor.get(step) if isinstance(cursor, dict) else None
        if not cursor:
            return None
        if not isinstance(cursor, str):
            raise CloudflareError(CollectionStatus.ERROR, "cursor de paginación no válido")
        if cursor == params.get(self._param):
            raise CloudflareError(CollectionStatus.ERROR, "cursor de paginación repetido")
        return {**params, self._param: cursor}


RULESETS_PAGER = CursorAfter("cursors", "after")
AUDIT_LOGS_PAGER = CursorAfter("cursor")


class CloudflareClient:
    def __init__(
        self,
        token: str,
        *,
        transport: httpx.BaseTransport | None = None,
        base_url: str = API_BASE,
    ) -> None:
        self._token = token
        self._http = httpx.Client(
            base_url=base_url,
            transport=transport,
            timeout=TIMEOUT_SECONDS,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            follow_redirects=False,
        )

    def __repr__(self) -> str:
        return "CloudflareClient()"

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._http.close()

    def get(self, path: str, params: dict[str, Any] | None = None) -> Reply:
        if not all(_PATH_SEGMENT.fullmatch(segment) for segment in path.strip("/").split("/")):
            raise CloudflareError(CollectionStatus.ERROR, "identificador no válido en la ruta")
        try:
            response = self._http.get(path, params=params)
        except httpx.HTTPError as exc:
            raise CloudflareError(
                CollectionStatus.ERROR, f"fallo de red: {type(exc).__name__}"
            ) from None
        return self._unwrap(response)

    def pages(self, path: str, pager: Pager | None = None, **fixed: Any) -> list[Any]:
        """Todos los elementos de un endpoint paginado; límite o página fallida -> error."""
        pager = pager or PageNumber()
        params = {**fixed, **pager.first()}
        items: list[Any] = []
        for _ in range(MAX_PAGES):
            reply = self.get(path, params)
            if not isinstance(reply.result, list):
                raise CloudflareError(CollectionStatus.ERROR, "respuesta sin lista de resultados")
            items.extend(reply.result)
            following = pager.next(params, reply)
            if following is None:
                return items
            params = following
        raise CloudflareError(CollectionStatus.ERROR, "límite de páginas alcanzado")

    def _unwrap(self, response: httpx.Response) -> Reply:
        envelope = self._envelope(response)
        if response.is_success and envelope is not None and envelope["success"] is True:
            info = envelope.get("result_info")
            return Reply(envelope["result"], info if isinstance(info, dict) else {})
        message = self._describe(response.status_code, envelope)
        if response.status_code == 401:
            raise CredentialRejected(message)
        if response.status_code == 403:
            raise CloudflareError(CollectionStatus.PERMISSION_DENIED, message)
        raise CloudflareError(CollectionStatus.ERROR, message)

    @staticmethod
    def _envelope(response: httpx.Response) -> dict[str, Any] | None:
        try:
            body = response.json()
        except ValueError:
            return None
        if (
            isinstance(body, dict)
            and isinstance(body.get("success"), bool)
            and isinstance(body.get("errors"), list)
            and "result" in body
        ):
            return body
        return None

    def _describe(self, http_status: int, envelope: dict[str, Any] | None) -> str:
        if envelope is None:
            return f"HTTP {http_status}: respuesta mal formada"
        parts = []
        for error in envelope["errors"][:3]:
            if isinstance(error, dict):
                code = error.get("code")
                code = code if isinstance(code, int) else "?"
                text = self._clean(str(error.get("message", "")))
                parts.append(f"{code}: {text}" if text else f"{code}")
        detail = "; ".join(parts) or "sin detalle"
        return f"HTTP {http_status}: {detail}"

    def _clean(self, text: str) -> str:
        text = _CONTROL.sub(" ", text.replace(self._token, "[redactado]"))
        return text[:MESSAGE_LIMIT]


def verify_token(client: CloudflareClient, *, kind: str, account_id: str) -> None:
    """Exige un token activo; sin fallback entre el endpoint de cuenta y el de usuario."""
    path = f"/accounts/{account_id}/tokens/verify" if kind == "account" else "/user/tokens/verify"
    try:
        reply = client.get(path)
    except CredentialRejected:
        raise
    except CloudflareError as exc:
        raise CredentialRejected(f"el token no se puede verificar: {exc.message}") from None
    state = reply.result.get("status") if isinstance(reply.result, dict) else None
    if state != "active":
        raise CredentialRejected("el token no está activo")
