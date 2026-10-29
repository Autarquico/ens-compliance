"""Cliente de solo lectura de la Admin SDK (Directory y Reports).

La base es fija (`https://admin.googleapis.com`) y no se siguen redirecciones. El token lo
gestiona `TokenProvider`; este módulo no lo guarda ni lo muestra. Los mensajes de error son
fijos por tipo de fallo: del cuerpo de un error solo se leen los códigos de motivo, que se
comparan con listas cerradas, nunca su texto.
"""

from collections.abc import Callable
from datetime import datetime
from typing import Any, Self
from urllib.parse import quote

import httpx

from ens_evidence.connectors.google_workspace.auth import (
    CredentialRejected,
    GoogleWorkspaceError,
    ServiceAccountKey,
    TokenProvider,
)
from ens_evidence.models.enums import CollectionStatus

API_BASE = "https://admin.googleapis.com"
MAX_PAGES = 100
TIMEOUT_SECONDS = 30.0
MAX_PAGE_TOKEN_LENGTH = 2048
QUOTA_REASONS = frozenset(
    {
        "rateLimitExceeded",
        "userRateLimitExceeded",
        "quotaExceeded",
        "dailyLimitExceeded",
        "limitExceeded",
        "RESOURCE_EXHAUSTED",
    }
)
# NO CONTRASTADO con la documentación: motivos con los que Google indicaría que la función
# no existe en la edición contratada. Solo estos producen NOT_SUPPORTED.
EDITION_REASONS = frozenset({"featureNotAvailable"})


def segment(value: str) -> str:
    if value in {"", ".", ".."}:
        raise GoogleWorkspaceError(CollectionStatus.ERROR, "identificador no válido en la ruta")
    return quote(value, safe="")


class WorkspaceClient:
    def __init__(
        self,
        key: ServiceAccountKey,
        subject: str,
        *,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], datetime],
    ) -> None:
        self._http = httpx.Client(
            base_url=API_BASE,
            transport=transport,
            timeout=TIMEOUT_SECONDS,
            follow_redirects=False,
        )
        self._tokens = TokenProvider(key, subject, self._http, clock)

    def __repr__(self) -> str:
        return "WorkspaceClient()"

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._http.close()

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        token = self._tokens.token()
        try:
            response = self._http.get(
                path, params=params, headers={"Authorization": f"Bearer {token}"}
            )
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            raise GoogleWorkspaceError(
                CollectionStatus.ERROR, f"fallo de red: {type(exc).__name__}"
            ) from None
        if not response.is_success:
            raise _failure(response)
        try:
            body = response.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise GoogleWorkspaceError(
                CollectionStatus.ERROR,
                f"HTTP {response.status_code}: respuesta mal formada",
                http_status=response.status_code,
            )
        return body

    def pages(self, path: str, wrapper: str, params: dict[str, Any] | None = None) -> list[Any]:
        """Elementos de todas las páginas, mientras haya `nextPageToken` (aunque venga vacía).

        Un fallo en cualquier página, un ciclo o el tope de páginas invalidan el conjunto.
        """
        items: list[Any] = []
        seen: set[str] = set()
        query = dict(params or {})
        for _ in range(MAX_PAGES):
            body = self.get(path, query)
            listing = body.get(wrapper, [])
            if not isinstance(listing, list):
                raise GoogleWorkspaceError(
                    CollectionStatus.ERROR, "respuesta sin lista de resultados"
                )
            items.extend(listing)
            token = body.get("nextPageToken")
            if token in (None, ""):
                return items
            if not isinstance(token, str) or len(token) > MAX_PAGE_TOKEN_LENGTH or token in seen:
                raise GoogleWorkspaceError(CollectionStatus.ERROR, "token de paginación no válido")
            seen.add(token)
            query["pageToken"] = token
        raise GoogleWorkspaceError(CollectionStatus.ERROR, "límite de páginas alcanzado")


def _reasons(response: httpx.Response) -> set[str]:
    try:
        body = response.json()
    except ValueError:
        return set()
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        return set()
    found: list[Any] = [error.get("status")]
    for key in ("errors", "details"):
        entries = error.get(key)
        found += (
            [e.get("reason") for e in entries if isinstance(e, dict)]
            if isinstance(entries, list)
            else []
        )
    return {reason for reason in found if isinstance(reason, str)}


def _failure(response: httpx.Response) -> GoogleWorkspaceError:
    code = response.status_code
    reasons = _reasons(response)

    def error(status: CollectionStatus, text: str, quota: bool = False) -> GoogleWorkspaceError:
        return GoogleWorkspaceError(status, f"HTTP {code}: {text}", http_status=code, quota=quota)

    if code == 401:
        return CredentialRejected("HTTP 401: credencial rechazada")
    if code == 429 or reasons & QUOTA_REASONS:
        return error(CollectionStatus.ERROR, "límite de peticiones alcanzado", quota=True)
    if code == 403:
        if reasons & EDITION_REASONS:
            return error(CollectionStatus.NOT_SUPPORTED, "función no disponible en la edición")
        return error(CollectionStatus.PERMISSION_DENIED, "permiso insuficiente")
    if 300 <= code < 400:
        return error(CollectionStatus.ERROR, "redirección no seguida")
    return error(CollectionStatus.ERROR, "error de la API")
