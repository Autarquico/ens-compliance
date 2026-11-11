"""Lectura del export de usuarios: solo lectura, sin symlinks, con tope y validada.

Un export rechazado no se guarda ni se cita: el único rastro es un código fijo.
"""

import json
import os
import stat
from pathlib import Path

from ens_evidence.kit.passwords_export import PasswordsExport

MAX_EXPORT_BYTES = 2 * 1024 * 1024

MISSING = "export_missing"
UNREADABLE = "export_unreadable"
NOT_REGULAR = "export_not_regular"
TOO_LARGE = "export_too_large"
INVALID = "export_invalid"
STALE = "export_stale"
FUTURE = "export_future"


class ReadFailure(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def read_export(path: str) -> PasswordsExport:
    """El export validado, o `ReadFailure` con un código fijo (nunca con valores del fichero)."""
    raw = _read_bytes(Path(path))
    try:
        return PasswordsExport.model_validate(json.loads(raw))
    except Exception:  # noqa: BLE001 - cualquier rechazo es el mismo código, sin el valor
        raise ReadFailure(INVALID) from None


def _read_bytes(path: Path) -> bytes:
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        raise ReadFailure(MISSING) from None
    except OSError:  # incluye ELOOP: la ruta es un symlink
        raise ReadFailure(UNREADABLE) from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ReadFailure(NOT_REGULAR)
        if info.st_size > MAX_EXPORT_BYTES:
            raise ReadFailure(TOO_LARGE)
        with os.fdopen(fd, "rb", closefd=False) as handle:
            raw = handle.read(MAX_EXPORT_BYTES + 1)
    except OSError:
        raise ReadFailure(UNREADABLE) from None
    finally:
        os.close(fd)
    if len(raw) > MAX_EXPORT_BYTES:
        raise ReadFailure(TOO_LARGE)
    return raw
