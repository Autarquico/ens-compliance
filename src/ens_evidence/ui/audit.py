"""Auditoría de las escrituras de la UI: quién (identidad de Access), qué y cuándo.

Nunca cuerpos de formulario ni valores: solo el paso, la acción y las claves escritas.
"""

import logging
import re
from datetime import datetime

AUDIT_LOGGER = logging.getLogger("ens_evidence.ui.audit")
_SAFE = re.compile(r"[A-Za-z0-9_.@+/:-]{1,128}")


def _field(value: str | None) -> str:
    if not value:
        return "-"
    return value if _SAFE.fullmatch(value) else "?"


def audit_write(
    *, identity: str, step: str, action: str | None, keys: list[str], at: datetime
) -> None:
    AUDIT_LOGGER.info(
        "audit outcome=saved identity=%s step=%s action=%s keys=%s at=%s",
        _field(identity),
        _field(step),
        _field(action),
        ",".join(_field(k) for k in keys) or "-",
        at.isoformat(),
    )
