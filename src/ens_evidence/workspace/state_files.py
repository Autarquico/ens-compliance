"""Escritura atómica de JSON en directorios de estado privados (un único escritor por fichero)."""

import json
import os
import tempfile
from pathlib import Path
from typing import Any

_OTHERS_CAN_WRITE = 0o022


def private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.stat()
    if info.st_uid != os.geteuid() or info.st_mode & _OTHERS_CAN_WRITE:
        raise PermissionError("el directorio de estado debe ser del usuario y no escribible")
    return path


def write_json(path: Path, document: Any) -> None:
    private_dir(path.parent)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(document, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
