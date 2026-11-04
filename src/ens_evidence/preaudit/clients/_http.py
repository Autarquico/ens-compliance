"""Transporte común de los clientes de API: solo HTTPS, sin redirecciones, timeout y reintentos
acotados (2, solo ante 429 y 5xx). La clave vive en las cabeceras del cliente `httpx`: no aparece
en `repr`, en excepciones ni en logs, y los mensajes de error son fijos por tipo de fallo.

`trust_env=False`: ni proxies (`HTTPS_PROXY`, `ALL_PROXY`…) ni CA (`SSL_CERT_FILE`…) del entorno,
que desviarían la conexión o la confianza TLS sin constar en el destino consentido. Quien necesite
un intermediario lo configura como URL base, y entonces sí consta."""

import json
import time
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlsplit

import httpx

from ens_evidence.preaudit.adapter import ProviderError, ProviderErrorKind

MAX_RETRIES = 2
MAX_WAIT_SECONDS = 30.0
USER_AGENT = "ens-evidence"


def normalize_base_url(base_url: str) -> str:
    """`https://host[:puerto][/ruta]` sin barra final; rechaza todo lo demás."""
    try:
        parts = urlsplit(base_url.strip())
        parts.port  # noqa: B018 - valida el puerto: lanza ValueError si está mal formado
    except ValueError:
        raise ProviderError(ProviderErrorKind.CONFIG, "URL base no válida") from None
    if parts.scheme != "https":
        raise ProviderError(ProviderErrorKind.CONFIG, "la URL base debe usar https")
    if not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise ProviderError(
            ProviderErrorKind.CONFIG, "la URL base no admite credenciales, consulta ni fragmento"
        )
    netloc = parts.hostname.lower() + (f":{parts.port}" if parts.port else "")
    return f"https://{netloc}{parts.path.rstrip('/')}"


def api_destination(name: str, base_url: str) -> str:
    """Destino al que se liga el consentimiento de un proveedor de API: `<proveedor> <URL base>`."""
    return f"{name} {normalize_base_url(base_url)}"


def api_key(env: Mapping[str, str], name: str) -> str:
    key = env.get(name, "").strip()
    if not key:
        raise ProviderError(ProviderErrorKind.CONFIG, f"falta la variable de entorno {name}")
    return key


class JsonPoster:
    def __init__(
        self,
        base_url: str,
        headers: Mapping[str, str],
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.base_url = normalize_base_url(base_url)
        self._sleep = sleep
        self._http = httpx.Client(
            headers={**headers, "content-type": "application/json", "user-agent": USER_AGENT},
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    def __repr__(self) -> str:
        return f"JsonPoster({self.base_url!r})"

    def post(self, path: str, body: dict[str, Any], timeout: float) -> Any:
        content = json.dumps(body, ensure_ascii=False).encode()
        url = f"{self.base_url}{path}"
        for attempt in range(MAX_RETRIES + 1):
            response = self._send(url, content, timeout)
            if not _retryable(response.status_code) or attempt == MAX_RETRIES:
                return _parse(response)
            self._sleep(_wait(response, attempt))
        raise AssertionError("unreachable")

    def _send(self, url: str, content: bytes, timeout: float) -> httpx.Response:
        try:
            return self._http.post(url, content=content, timeout=timeout)
        except httpx.TimeoutException:
            raise ProviderError(
                ProviderErrorKind.TIMEOUT, "el proveedor no respondió a tiempo"
            ) from None
        except (httpx.HTTPError, httpx.InvalidURL):
            raise ProviderError(
                ProviderErrorKind.TRANSPORT, "no se pudo conectar con el proveedor"
            ) from None


def _retryable(status: int) -> bool:
    return status == 429 or status >= 500


def _wait(response: httpx.Response, attempt: int) -> float:
    retry_after = response.headers.get("retry-after", "")
    try:
        seconds = float(retry_after)
    except ValueError:
        seconds = 2.0**attempt
    return min(max(seconds, 0.0), MAX_WAIT_SECONDS)


def _parse(response: httpx.Response) -> Any:
    status = response.status_code
    if 300 <= status < 400:
        raise ProviderError(
            ProviderErrorKind.REDIRECT,
            "el proveedor respondió con una redirección; no se sigue",
            http_status=status,
        )
    if status in (401, 403):
        raise ProviderError(
            ProviderErrorKind.HTTP, "el proveedor rechazó la credencial", http_status=status
        )
    if status >= 400:
        raise ProviderError(
            ProviderErrorKind.HTTP,
            f"el proveedor respondió con error HTTP {status}",
            http_status=status,
        )
    try:
        return response.json()
    except ValueError:
        raise ProviderError(
            ProviderErrorKind.RESPONSE, "respuesta del proveedor ilegible"
        ) from None
