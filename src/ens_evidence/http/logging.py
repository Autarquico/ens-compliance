"""Registro de peticiones: solo resultado, endpoint, activo y un hash corto del host.

Nunca cuerpos, secretos, node keys ni tokens: la función no acepta nada más.
"""

import copy
import logging
import re
from typing import Any

_SAFE = re.compile(r"[A-Za-z0-9_./:-]{1,64}")


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
    host: str | None = None,
) -> None:
    """`host` es la huella corta del equipo, nunca su identificador."""
    logger.info(
        "outcome=%s endpoint=%s asset=%s host=%s",
        _field(outcome),
        _field(endpoint),
        _field(asset_id),
        _field(host),
    )


class WithoutTraceback(logging.Filter):
    """Quita la traza de cada registro: puede incluir valores del cuerpo de la petición."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


def uvicorn_log_config() -> dict[str, Any]:
    """Configuración de logs de uvicorn sin trazas (uvicorn vuelca la de un 500 re-lanzado)."""
    from uvicorn.config import LOGGING_CONFIG

    config = copy.deepcopy(LOGGING_CONFIG)
    config["filters"] = {"without_traceback": {"()": f"{__name__}.WithoutTraceback"}}
    for handler in config["handlers"].values():
        handler["filters"] = ["without_traceback"]
    return config
