"""Refresco de los certificados (JWKS) de Cloudflare Access para la interfaz web (ADR 0021).

`ui` no tiene salida a Internet: este trabajo, en `app`, descarga
`https://<team>.cloudflareaccess.com/cdn-cgi/access/certs`, valida cada clave y publica el
fichero de forma atómica. Cualquier fallo conserva el fichero anterior y deja un código estable
en el log (nunca el mensaje de la excepción, la URL ni el cuerpo recibido).
"""

import base64
import json
import logging
import os
import re
import tempfile
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from cryptography.hazmat.primitives.asymmetric import rsa

TEAM_DOMAIN_ENV = "ENS_ACCESS_JWKS_TEAM_DOMAIN"
OUTPUT_ENV = "ENS_ACCESS_JWKS_FILE"
CERTS_PATH = "/cdn-cgi/access/certs"
TOTAL_TIMEOUT_SECONDS = 10.0
MAX_BODY_BYTES = 64 * 1024
MAX_KEYS = 20
MIN_KEY_BITS = 2048
MAX_KID_LENGTH = 256
REFRESH_EVERY_SECONDS = 6 * 3600.0
RETRY_AFTER_FAILURE_SECONDS = 15 * 60.0
PUBLISHED_MODE = 0o644

_TEAM_HOST = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.cloudflareaccess\.com")
_LOGGER = logging.getLogger(__name__)

ClientFactory = Callable[[], httpx.Client]


class RefreshFailed(Exception):
    """El refresco no se pudo completar; `code` es estable y seguro para el log."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class JwksRefreshConfig:
    team_domain: str
    output: Path

    @property
    def url(self) -> str:
        return f"https://{self.team_domain}{CERTS_PATH}"


def config_from_env(env: Mapping[str, str]) -> JwksRefreshConfig | None:
    """None si no hay configuración (el trabajo no se registra) o es inválida (también se avisa)."""
    team = env.get(TEAM_DOMAIN_ENV, "").strip().lower()
    output = env.get(OUTPUT_ENV, "").strip()
    if not team and not output:
        _LOGGER.info("refresco del JWKS de Access no configurado; no se registra")
        return None
    if not _TEAM_HOST.fullmatch(team) or not output or not Path(output).is_absolute():
        _LOGGER.warning("refresco del JWKS de Access: configuración inválida (config_invalid)")
        return None
    return JwksRefreshConfig(team_domain=team, output=Path(output))


def default_client() -> httpx.Client:
    return httpx.Client(
        timeout=httpx.Timeout(TOTAL_TIMEOUT_SECONDS),
        follow_redirects=False,
        trust_env=False,
    )


def refresh_once(config: JwksRefreshConfig, client_factory: ClientFactory = default_client) -> str:
    """Un refresco. Devuelve "ok" o el código del fallo; nunca lanza."""
    try:
        body = _download(config.url, client_factory)
        keys = _validated_keys(body)
        _publish(config.output, keys)
    except RefreshFailed as failure:
        return _failed(failure.code)
    except Exception as error:  # noqa: BLE001 - el trabajo nunca debe tumbar el scheduler
        _LOGGER.warning("refresco del JWKS: error inesperado (%s)", type(error).__name__)
        return "unexpected"
    _LOGGER.info("refresco del JWKS de Access: ok (%d claves)", len(keys))
    return "ok"


def _failed(code: str) -> str:
    _LOGGER.warning("refresco del JWKS de Access fallido (%s); se conserva el anterior", code)
    return code


def _download(url: str, client_factory: ClientFactory) -> bytes:
    deadline = time.monotonic() + TOTAL_TIMEOUT_SECONDS
    try:
        with client_factory() as client, client.stream("GET", url, follow_redirects=False) as resp:
            if resp.is_redirect:
                raise RefreshFailed("redirect")
            if resp.status_code != 200:
                raise RefreshFailed("bad_status")
            chunks: list[bytes] = []
            size = 0
            for chunk in resp.iter_bytes():
                size += len(chunk)
                if size > MAX_BODY_BYTES:
                    raise RefreshFailed("too_large")
                if time.monotonic() > deadline:
                    raise RefreshFailed("timeout")
                chunks.append(chunk)
    except httpx.TimeoutException:
        raise RefreshFailed("timeout") from None
    except httpx.HTTPError:
        raise RefreshFailed("network_error") from None
    return b"".join(chunks)


def _validated_keys(body: bytes) -> list[dict[str, str]]:
    try:
        document = json.loads(body)
    except ValueError:
        raise RefreshFailed("bad_json") from None
    raw = document.get("keys") if isinstance(document, dict) else None
    if not isinstance(raw, list) or not raw:
        raise RefreshFailed("bad_keys")
    if len(raw) > MAX_KEYS:
        raise RefreshFailed("too_many_keys")
    keys = [_validated_key(item) for item in raw]
    if len({key["kid"] for key in keys}) != len(keys):
        raise RefreshFailed("duplicate_kid")
    return keys


def _validated_key(item: Any) -> dict[str, str]:
    if not isinstance(item, dict) or item.get("kty") != "RSA":
        raise RefreshFailed("bad_keys")
    kid, n, e = item.get("kid"), item.get("n"), item.get("e")
    if not (isinstance(kid, str) and kid and len(kid) <= MAX_KID_LENGTH and kid.isprintable()):
        raise RefreshFailed("bad_keys")
    if item.get("alg", "RS256") != "RS256" or item.get("use", "sig") != "sig":
        raise RefreshFailed("bad_keys")
    try:
        public = rsa.RSAPublicNumbers(_b64_int(e), _b64_int(n)).public_key()
    except (TypeError, ValueError):
        raise RefreshFailed("bad_keys") from None
    if public.key_size < MIN_KEY_BITS:
        raise RefreshFailed("weak_key")
    return {"kty": "RSA", "kid": kid, "use": "sig", "alg": "RS256", "n": n, "e": e}


def _b64_int(value: object) -> int:
    if not isinstance(value, str) or not value.isascii():
        raise ValueError("no es base64url")
    return int.from_bytes(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)), "big")


def _publish(output: Path, keys: list[dict[str, str]]) -> None:
    payload = json.dumps({"keys": keys}, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    tmp_name: str | None = None
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=output.parent, prefix=f".{output.name}.", suffix=".tmp")
        with os.fdopen(fd, "wb") as handle:
            os.fchmod(handle.fileno(), PUBLISHED_MODE)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, output)
        tmp_name = None
    except OSError:
        raise RefreshFailed("write_failed") from None
    finally:
        if tmp_name is not None:
            Path(tmp_name).unlink(missing_ok=True)


class JwksRefreshLoop(threading.Thread):
    """Refresca al arrancar, cada 6 h y, tras un fallo, a los 15 min."""

    def __init__(
        self,
        config: JwksRefreshConfig,
        stop: threading.Event,
        client_factory: ClientFactory = default_client,
    ) -> None:
        super().__init__(name="access-jwks-refresh", daemon=True)
        self._config = config
        self._stop_requested = stop
        self._client_factory = client_factory

    def run(self) -> None:
        delay = 0.0
        while not self._stop_requested.wait(delay):
            ok = refresh_once(self._config, self._client_factory) == "ok"
            delay = REFRESH_EVERY_SECONDS if ok else RETRY_AFTER_FAILURE_SECONDS


@contextmanager
def jwks_refresh_alongside(
    stop: threading.Event, env: Mapping[str, str] | None = None
) -> Iterator[None]:
    """Hilo de refresco junto al planificador; sin configuración no arranca nada."""
    config = config_from_env(os.environ if env is None else env)
    loop = JwksRefreshLoop(config, stop) if config else None
    if loop:
        loop.start()
    try:
        yield
    finally:
        if loop:
            stop.set()
            loop.join(timeout=TOTAL_TIMEOUT_SECONDS + 2)
