"""Registro de peticiones: solo resultado, endpoint, activo y un hash corto del host.

Nunca cuerpos, secretos, node keys ni tokens: la función no acepta nada más.
"""

import hashlib
import logging
import re

_SAFE = re.compile(r"[A-Za-z0-9_./:-]{1,64}")


def host_hash(host_identifier: str) -> str:
    return hashlib.sha256(host_identifier.encode("utf-8")).hexdigest()[:8]


def _field(value: str | None) -> str:
    if value is None:
        return "-"
    return value if _SAFE.fullmatch(value) else "?"


def log_request(
    logger: logging.Logger,
    *,
    outcome: str,
    endpoint: str,
    asset_id: str | None = None,
    host_identifier: str | None = None,
) -> None:
    host = host_hash(host_identifier) if host_identifier else None
    logger.info(
        "outcome=%s endpoint=%s asset=%s host=%s",
        _field(outcome),
        _field(endpoint),
        _field(asset_id),
        _field(host),
    )
