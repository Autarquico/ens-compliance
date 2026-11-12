"""Verificación del JWT de Cloudflare Access (RS256) y middleware que exige identidad."""

import base64
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

ACCESS_HEADER = "cf-access-jwt-assertion"
CLOCK_SKEW_SECONDS = 60
HEALTH_PATH = "/healthz"
MAX_TOKEN_BYTES = 8192

JwksLoader = Callable[[], dict[str, Any]]


class AccessDenied(Exception):
    """El JWT no es aceptable; `reason` es un código estable que no incluye datos del token."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Identity:
    email: str


def load_jwks_file(path: Path) -> dict[str, Any]:
    try:
        jwks = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise AccessDenied("jwks_unavailable") from None
    if not isinstance(jwks, dict) or not isinstance(jwks.get("keys"), list):
        raise AccessDenied("jwks_unavailable")
    return jwks


class AccessVerifier:
    def __init__(
        self, *, issuer: str, audience: str, jwks_loader: JwksLoader, clock: Callable[[], datetime]
    ) -> None:
        self._issuer = issuer
        self._audience = audience
        self._jwks_loader = jwks_loader
        self._clock = clock

    def verify(self, token: str) -> Identity:
        header, claims, signing_input, signature = _split(token)
        if header.get("alg") != "RS256":
            raise AccessDenied("bad_alg")
        key = self._key_for(header.get("kid"))
        try:
            key.verify(signature, signing_input, padding.PKCS1v15(), hashes.SHA256())
        except InvalidSignature:
            raise AccessDenied("bad_signature") from None
        self._check_claims(claims)
        email = claims.get("email")
        if not isinstance(email, str) or not email:
            raise AccessDenied("no_identity")
        return Identity(email=email)

    def _key_for(self, kid: object) -> rsa.RSAPublicKey:
        if not isinstance(kid, str):
            raise AccessDenied("unknown_kid")
        try:
            keys = self._jwks_loader().get("keys", [])
        except AccessDenied:
            raise
        except Exception:
            raise AccessDenied("jwks_unavailable") from None
        for jwk in keys:
            if isinstance(jwk, dict) and jwk.get("kid") == kid and jwk.get("kty") == "RSA":
                return _public_key(jwk)
        raise AccessDenied("unknown_kid")

    def _check_claims(self, claims: dict[str, Any]) -> None:
        now = self._clock().timestamp()
        if claims.get("iss") != self._issuer:
            raise AccessDenied("bad_iss")
        aud = claims.get("aud")
        audiences = [aud] if isinstance(aud, str) else aud if isinstance(aud, list) else []
        if self._audience not in audiences:
            raise AccessDenied("bad_aud")
        exp = _number(claims.get("exp"))
        if exp is None or now > exp + CLOCK_SKEW_SECONDS:
            raise AccessDenied("expired")
        nbf = _number(claims.get("nbf"))
        if nbf is not None and now < nbf - CLOCK_SKEW_SECONDS:
            raise AccessDenied("not_yet_valid")


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _b64(segment: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))
    except ValueError:
        raise AccessDenied("malformed") from None


def _json_object(segment: str) -> dict[str, Any]:
    try:
        value = json.loads(_b64(segment))
    except ValueError:
        raise AccessDenied("malformed") from None
    if not isinstance(value, dict):
        raise AccessDenied("malformed")
    return value


def _split(token: str) -> tuple[dict[str, Any], dict[str, Any], bytes, bytes]:
    if len(token) > MAX_TOKEN_BYTES or not token.isascii():
        raise AccessDenied("malformed")
    parts = token.split(".")
    if len(parts) != 3:
        raise AccessDenied("malformed")
    header = _json_object(parts[0])
    claims = _json_object(parts[1])
    return header, claims, f"{parts[0]}.{parts[1]}".encode("ascii"), _b64(parts[2])


def _public_key(jwk: dict[str, Any]) -> rsa.RSAPublicKey:
    try:
        n = int.from_bytes(_b64(jwk["n"]), "big")
        e = int.from_bytes(_b64(jwk["e"]), "big")
        return rsa.RSAPublicNumbers(e, n).public_key()
    except (KeyError, TypeError, ValueError):
        raise AccessDenied("unknown_kid") from None


class AccessMiddleware:
    """Exige un JWT de Access válido en cada petición HTTP salvo `/healthz`.

    La identidad queda en `request.state.identity`.
    """

    def __init__(self, app: ASGIApp, *, verifier: AccessVerifier) -> None:
        self._app = app
        self._verifier = verifier

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] == HEALTH_PATH:
            await self._app(scope, receive, send)
            return
        token = _header(scope, ACCESS_HEADER)
        try:
            if token is None:
                raise AccessDenied("missing")
            identity = self._verifier.verify(token)
        except AccessDenied:
            response = JSONResponse({"error": "unauthorized"}, status_code=401)
            await response(scope, receive, send)
            return
        scope.setdefault("state", {})["identity"] = identity
        await self._app(scope, receive, send)


def _header(scope: Scope, name: str) -> str | None:
    wanted = name.encode("ascii")
    for key, value in scope["headers"]:
        if key == wanted:
            return value.decode("latin-1")
    return None
