"""Cliente de solo lectura de la API REST de GitHub.

El token vive solo aquí: no aparece en `repr`, en excepciones ni en logs. Los mensajes de
error son fijos por tipo de fallo (nunca texto de la API); el cuerpo de un error solo se
compara con los mensajes positivos de `_KNOWN_FINDINGS`.
"""

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Self
from urllib.parse import parse_qsl, quote, unquote, urlsplit

import httpx

from ens_evidence.models.enums import CollectionStatus

API_BASE = "https://api.github.com"
API_HOST = "api.github.com"
API_VERSION = "2022-11-28"
USER_AGENT = "ens-evidence"
PER_PAGE = 100
MAX_PAGES = 100
TIMEOUT_SECONDS = 30.0
PAGINATION_PARAMS = frozenset({"page", "per_page", "cursor", "before", "after"})
# Raíz de la ruta pedida -> (raíz canónica por id, segmentos que la identifican por nombre).
CANONICAL_ROOTS = {"orgs": ("organizations", 2), "repos": ("repositories", 3)}
PRINTABLE_ASCII = re.compile(r"[!-~]+")


class Finding(StrEnum):
    """Mensajes positivos de GitHub que sí demuestran un estado (no un 404 genérico)."""

    BRANCH_NOT_PROTECTED = "branch_not_protected"
    DEPENDABOT_ALERTS_DISABLED = "dependabot_alerts_disabled"


class GitHubError(Exception):
    """Fallo saneado: el mensaje es apto para evidencias, logs y usuario."""

    def __init__(
        self,
        status: CollectionStatus,
        message: str,
        *,
        http_status: int | None = None,
        finding: Finding | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.http_status = http_status
        self.finding = finding


class CredentialRejected(GitHubError):
    """El token no es válido, ha caducado o ha sido revocado."""

    def __init__(self, message: str) -> None:
        super().__init__(CollectionStatus.ERROR, message, http_status=401)


@dataclass(frozen=True)
class Reply:
    http_status: int
    body: Any = None


def _known_finding(http_status: int, message: str) -> Finding | None:
    if http_status == 404 and message == "Branch not protected":
        return Finding.BRANCH_NOT_PROTECTED
    if http_status == 403 and message.startswith("Dependabot alerts are disabled"):
        return Finding.DEPENDABOT_ALERTS_DISABLED
    return None


def _segment(value: str | int) -> str:
    text = str(value)
    if text in {"", ".", ".."}:
        raise GitHubError(CollectionStatus.ERROR, "identificador no válido en la ruta")
    return quote(text, safe="")


def _page_key(path: str, pairs: list[tuple[str, str]]) -> tuple[str, tuple[tuple[str, str], ...]]:
    return path, tuple(sorted(pairs))


def _next_page_key(
    url: str, origin: list[str], fixed: dict[str, set[str]], allowed_keys: frozenset[str] | set[str]
) -> tuple[tuple[str, tuple[tuple[str, str], ...]], str | None] | None:
    """Clave de la página siguiente y su id canónico, si `url` es la ruta pedida o la canónica.

    Los segmentos se comparan crudos (`%2F` dentro de una rama no es un separador).
    """
    if not PRINTABLE_ASCII.fullmatch(url):
        return None
    try:
        parts = urlsplit(url)
        parts.port  # noqa: B018 - valida el puerto: lanza ValueError si está mal formado
    except ValueError:
        return None
    if parts.scheme != "https" or parts.netloc.lower() != API_HOST or parts.fragment:
        return None
    segments = parts.path.split("/")
    if segments[0] != "" or not (match := _match_resource(segments[1:], origin)):
        return None
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    if not {name for name, _ in pairs} <= allowed_keys:
        return None
    if any(name in fixed and value not in fixed[name] for name, value in pairs):
        return None
    return _page_key(parts.path, pairs), match[0]


def _match_resource(segments: list[str], origin: list[str]) -> tuple[str | None] | None:
    """`(None,)` si es la ruta pedida, `(id,)` si es la canónica por id, None si es otro recurso."""
    if any(unquote(s) in {"", ".", ".."} or "\\" in unquote(s) for s in segments):
        return None
    if segments == origin:
        return (None,)
    canonical = CANONICAL_ROOTS.get(origin[0])
    if canonical is None:
        return None
    root, named = canonical
    if (
        len(segments) >= 2
        and segments[0] == root
        and segments[1].isascii()
        and segments[1].isdigit()
        and segments[2:] == origin[named:]
    ):
        return (segments[1],)
    return None


def path_of(*segments: str | int) -> str:
    return "/" + "/".join(_segment(segment) for segment in segments)


class GitHubClient:
    def __init__(self, token: str, *, transport: httpx.BaseTransport | None = None) -> None:
        self._http = httpx.Client(
            base_url=API_BASE,
            transport=transport,
            timeout=TIMEOUT_SECONDS,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": API_VERSION,
                "User-Agent": USER_AGENT,
            },
            follow_redirects=False,
        )

    def __repr__(self) -> str:
        return "GitHubClient()"

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._http.close()

    def get(self, *segments: str | int, params: dict[str, Any] | None = None) -> Reply:
        reply, _ = self._fetch(path_of(*segments), params)
        return reply

    def pages(
        self,
        *segments: str | int,
        params: dict[str, Any] | None = None,
        wrapper: str | None = None,
    ) -> list[Any]:
        """Todos los elementos de un endpoint paginado por `Link: rel="next"`.

        `wrapper`: clave que contiene la lista en respuestas envueltas
        (`{"total_count": n, "<wrapper>": [...]}`). Un fallo en cualquier página, un
        ciclo o el tope de páginas invalidan el conjunto.
        """
        target = path_of(*segments)
        query: dict[str, Any] | None = {"per_page": PER_PAGE, **(params or {})}
        origin = target[1:].split("/")
        allowed_keys = PAGINATION_PARAMS | set(query)
        fixed: dict[str, set[str]] = {}
        for name, value in httpx.QueryParams(query).multi_items():
            if name not in PAGINATION_PARAMS:
                fixed.setdefault(name, set()).add(value)
        canonical_id: str | None = None
        seen = {_page_key(target, httpx.QueryParams(query).multi_items())}
        items: list[Any] = []
        for _ in range(MAX_PAGES):
            reply, next_url = self._fetch(target, query)
            items.extend(self._items(reply.body, wrapper))
            if next_url is None:
                return items
            following = _next_page_key(next_url, origin, fixed, allowed_keys)
            if following is None or following[0] in seen:
                raise GitHubError(CollectionStatus.ERROR, "enlace de paginación no válido")
            key, resource_id = following
            if resource_id is not None:
                if canonical_id not in (None, resource_id):
                    raise GitHubError(CollectionStatus.ERROR, "enlace de paginación no válido")
                canonical_id = resource_id
            seen.add(key)
            target, query = next_url, None
        raise GitHubError(CollectionStatus.ERROR, "límite de páginas alcanzado")

    @staticmethod
    def _items(body: Any, wrapper: str | None) -> list[Any]:
        listing = body.get(wrapper) if wrapper and isinstance(body, dict) else body
        if not isinstance(listing, list):
            raise GitHubError(CollectionStatus.ERROR, "respuesta sin lista de resultados")
        return listing

    def _fetch(self, target: str, params: dict[str, Any] | None) -> tuple[Reply, str | None]:
        try:
            response = self._http.get(target, params=params)
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            raise GitHubError(
                CollectionStatus.ERROR, f"fallo de red: {type(exc).__name__}"
            ) from None
        if response.is_success:
            return self._reply(response), response.links.get("next", {}).get("url")
        raise self._failure(response)

    @staticmethod
    def _reply(response: httpx.Response) -> Reply:
        if response.status_code == 204 or not response.content:
            return Reply(response.status_code)
        try:
            return Reply(response.status_code, response.json())
        except ValueError:
            raise GitHubError(
                CollectionStatus.ERROR,
                f"HTTP {response.status_code}: respuesta mal formada",
                http_status=response.status_code,
            ) from None

    @staticmethod
    def _failure(response: httpx.Response) -> GitHubError:
        code = response.status_code
        message = _api_message(response)

        def error(status: CollectionStatus, text: str, finding: Finding | None = None):
            return GitHubError(status, f"HTTP {code}: {text}", http_status=code, finding=finding)

        if code == 401:
            return CredentialRejected("HTTP 401: credencial rechazada")
        if _is_rate_limited(response, message):
            return error(CollectionStatus.ERROR, "límite de peticiones alcanzado")
        if code == 403:
            finding = _known_finding(code, message)
            return error(CollectionStatus.PERMISSION_DENIED, "permiso insuficiente", finding)
        if code == 404:
            finding = _known_finding(code, message)
            return error(CollectionStatus.PERMISSION_DENIED, "no encontrado o sin acceso", finding)
        if 300 <= code < 400:
            return error(CollectionStatus.ERROR, "redirección no seguida")
        return error(CollectionStatus.ERROR, "error de la API")


def _api_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return ""
    message = body.get("message") if isinstance(body, dict) else None
    return message if isinstance(message, str) else ""


def _is_rate_limited(response: httpx.Response, message: str) -> bool:
    """Límite primario (cuota a 0) o secundario (`retry-after` o su mensaje)."""
    if response.status_code == 429:
        return True
    if response.status_code != 403:
        return False
    return (
        response.headers.get("x-ratelimit-remaining") == "0"
        or "retry-after" in response.headers
        or "rate limit" in message.lower()
    )


def verify_token(client: GitHubClient) -> None:
    """Exige un token aceptado por GitHub (`GET /user`); cualquier fallo invalida la recogida."""
    try:
        reply = client.get("user")
    except CredentialRejected:
        raise
    except GitHubError as exc:
        raise CredentialRejected(f"el token no se puede verificar: {exc.message}") from None
    if not isinstance(reply.body, dict):
        raise CredentialRejected("el token no se puede verificar: respuesta mal formada")
