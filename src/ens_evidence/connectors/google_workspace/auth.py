"""Credencial de la cuenta de servicio y token de acceso (JWT bearer, RFC 7523).

La clave privada y el token viven solo aquí: no aparecen en `repr`, excepciones ni logs.
Del fichero de credenciales solo se leen `client_email` y `private_key`; cualquier URL que
traiga (`token_uri`...) se ignora y el destino del token es fijo. `cryptography` es una
dependencia opcional (`ens-evidence[workspace]`) y se importa al firmar.
"""

import base64
import json
import re
from collections.abc import Callable
from datetime import datetime
from typing import Any

import httpx

from ens_evidence.models.enums import CollectionStatus

TOKEN_URL = "https://oauth2.googleapis.com/token"
GRANT_TYPE = "urn:ietf:params:oauth:grant-type:jwt-bearer"
SCOPES = (
    "https://www.googleapis.com/auth/admin.directory.user.readonly",
    "https://www.googleapis.com/auth/admin.directory.rolemanagement.readonly",
    "https://www.googleapis.com/auth/admin.reports.audit.readonly",
    "https://www.googleapis.com/auth/admin.directory.customer.readonly",
)
ASSERTION_LIFETIME_SECONDS = 3600
RENEWAL_MARGIN_SECONDS = 60
MAX_TOKEN_LIFETIME_SECONDS = 3600
TIMEOUT_SECONDS = 30.0
SERVICE_ACCOUNT_EMAIL = re.compile(r"[A-Za-z0-9._%+-]{1,100}@[A-Za-z0-9.-]{1,253}")
# Códigos de error del endpoint de token (RFC 6749) -> motivo fijo.
_TOKEN_ERRORS = {
    "invalid_grant": "credencial revocada o no válida (invalid_grant)",
    "unauthorized_client": "delegación en todo el dominio no autorizada (unauthorized_client)",
    "invalid_scope": "scope no autorizado (invalid_scope)",
    "invalid_client": "cuenta de servicio no válida (invalid_client)",
    "access_denied": "acceso denegado (access_denied)",
}


class GoogleWorkspaceError(Exception):
    """Fallo saneado: el mensaje es apto para evidencias, logs y usuario."""

    def __init__(
        self,
        status: CollectionStatus,
        message: str,
        *,
        http_status: int | None = None,
        quota: bool = False,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.http_status = http_status
        self.quota = quota


class CollectionAborted(GoogleWorkspaceError):
    """Hecho que invalida la recogida entera, no solo la sección que lo detecta."""

    def __init__(self, message: str) -> None:
        super().__init__(CollectionStatus.ERROR, message)


class CredentialRejected(CollectionAborted):
    """La credencial no es válida, ha caducado o ha sido revocada."""


class ServiceAccountKey:
    """`client_email` y clave privada PEM de la cuenta de servicio."""

    __slots__ = ("_private_key", "client_email")

    def __init__(self, raw_json: str) -> None:
        try:
            raw = json.loads(raw_json)
        except ValueError:
            raise CredentialRejected("la clave de la cuenta de servicio no es JSON") from None
        email = raw.get("client_email") if isinstance(raw, dict) else None
        pem = raw.get("private_key") if isinstance(raw, dict) else None
        if not (isinstance(email, str) and SERVICE_ACCOUNT_EMAIL.fullmatch(email)):
            raise CredentialRejected("la clave de la cuenta de servicio no tiene client_email")
        if not (isinstance(pem, str) and "PRIVATE KEY" in pem):
            raise CredentialRejected("la clave de la cuenta de servicio no tiene private_key")
        self.client_email = email
        self._private_key = pem

    def __repr__(self) -> str:
        return "ServiceAccountKey()"

    def sign(self, message: bytes) -> bytes:
        """Firma RS256 (RSASSA-PKCS1-v1_5 con SHA-256)."""
        try:
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import padding, rsa
        except ImportError:
            raise CredentialRejected(
                "falta la dependencia opcional cryptography: instala ens-evidence[workspace]"
            ) from None
        try:
            private_key = serialization.load_pem_private_key(
                self._private_key.encode(), password=None
            )
            if not isinstance(private_key, rsa.RSAPrivateKey):
                raise TypeError
            return private_key.sign(message, padding.PKCS1v15(), hashes.SHA256())
        except Exception:
            raise CredentialRejected(
                "la clave privada de la cuenta de servicio no es válida"
            ) from None


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def build_assertion(key: ServiceAccountKey, subject: str, issued_at: datetime) -> str:
    """JWT firmado que suplanta a `subject` con los scopes de solo lectura."""
    issued = int(issued_at.timestamp())
    segments = [
        _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode()),
        _b64url(
            json.dumps(
                {
                    "iss": key.client_email,
                    "sub": subject,
                    "scope": " ".join(SCOPES),
                    "aud": TOKEN_URL,
                    "iat": issued,
                    "exp": issued + ASSERTION_LIFETIME_SECONDS,
                },
                separators=(",", ":"),
            ).encode()
        ),
    ]
    signature = key.sign(".".join(segments).encode())
    return ".".join([*segments, _b64url(signature)])


class TokenProvider:
    """Token de acceso en memoria; se renueva al caducar y nunca se serializa."""

    def __init__(
        self,
        key: ServiceAccountKey,
        subject: str,
        http: httpx.Client,
        clock: Callable[[], datetime],
    ) -> None:
        self._key = key
        self._subject = subject
        self._http = http
        self._clock = clock
        self._token: str | None = None
        self._expires_at = 0.0

    def __repr__(self) -> str:
        return "TokenProvider()"

    def token(self) -> str:
        now = self._clock()
        if self._token is None or now.timestamp() >= self._expires_at - RENEWAL_MARGIN_SECONDS:
            self._token, lifetime = self._request(now)
            self._expires_at = now.timestamp() + lifetime
        return self._token

    def _request(self, now: datetime) -> tuple[str, int]:
        assertion = build_assertion(self._key, self._subject, now)
        try:
            response = self._http.post(
                TOKEN_URL, data={"grant_type": GRANT_TYPE, "assertion": assertion}
            )
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            raise GoogleWorkspaceError(
                CollectionStatus.ERROR, f"fallo de red al pedir el token: {type(exc).__name__}"
            ) from None
        if not response.is_success:
            raise _token_failure(response)
        body = _json(response)
        token = body.get("access_token")
        lifetime = body.get("expires_in")
        if not (isinstance(token, str) and token.isascii() and token.isprintable() and token):
            raise CredentialRejected("el endpoint de token devolvió una respuesta no válida")
        if not isinstance(lifetime, int) or isinstance(lifetime, bool) or lifetime <= 0:
            lifetime = MAX_TOKEN_LIFETIME_SECONDS
        return token, min(lifetime, MAX_TOKEN_LIFETIME_SECONDS)


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        body = None
    return body if isinstance(body, dict) else {}


def _token_failure(response: httpx.Response) -> GoogleWorkspaceError:
    code = response.status_code
    if code >= 500 or code == 429:
        return GoogleWorkspaceError(
            CollectionStatus.ERROR,
            f"HTTP {code}: el endpoint de token no está disponible",
            quota=code == 429,
        )
    error = _json(response).get("error")
    reason = _TOKEN_ERRORS.get(error) if isinstance(error, str) else None
    return CredentialRejected(f"HTTP {code}: {reason or 'token rechazado'}")
