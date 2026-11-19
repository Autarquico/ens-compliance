"""Auditoría de las escrituras de la UI: quién (identidad de Access), qué y cuándo.

Nunca cuerpos de formulario ni valores: solo el paso, la acción y las claves escritas.
"""

import logging
import re
from collections.abc import Iterable
from datetime import datetime

AUDIT_LOGGER = logging.getLogger("ens_evidence.ui.audit")
_SAFE = re.compile(r"[A-Za-z0-9_.@+/:-]{1,128}")


def _field(value: str | None) -> str:
    if not value:
        return "-"
    return value if _SAFE.fullmatch(value) else "?"


def _roles(roles: Iterable[str]) -> str:
    return ",".join(_field(str(r)) for r in roles) or "-"


def audit_write(
    *,
    identity: str,
    step: str,
    action: str | None,
    keys: list[str],
    at: datetime,
    roles: Iterable[str] = (),
    asset: str | None = None,
    failed: bool = False,
    rejected: bool = False,
) -> None:
    """`asset` identifica el activo tocado; `failed`, una escritura que falló tras escribir algo;
    `rejected`, una petición que el handler rechazó antes de escribir (sin claves)."""
    if rejected and failed:
        raise ValueError("una línea de auditoría no puede ser a la vez rejected y failed")
    target = f" asset={_field(asset)}" if asset is not None else ""
    outcome = "rejected" if rejected else "failed" if failed else "saved"
    (AUDIT_LOGGER.error if failed else AUDIT_LOGGER.warning if rejected else AUDIT_LOGGER.info)(
        "audit outcome=%s identity=%s step=%s action=%s%s roles=%s keys=%s at=%s",
        outcome,
        _field(identity),
        _field(step),
        _field(action),
        target,
        _roles(roles),
        "-" if rejected else ",".join(_field(k) for k in keys) or "-",
        at.isoformat(),
    )


def audit_denied(
    *, identity: str, method: str, path: str, reason: str, role: str | None, at: datetime
) -> None:
    """Una operación rechazada antes de escribir: quién, qué ruta, por qué y qué rol faltaba."""
    AUDIT_LOGGER.warning(
        "audit outcome=denied identity=%s method=%s path=%s reason=%s role=%s at=%s",
        _field(identity),
        _field(method),
        _field(path),
        _field(reason),
        _field(role),
        at.isoformat(),
    )
