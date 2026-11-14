"""Ficheros de `state/monitoring`: escritura atómica, privados, un escritor por fichero."""

import json
import os
import tempfile
from pathlib import Path
from typing import Any

MONITORING_DIR = "monitoring"


def monitoring_dir(state_dir: Path) -> Path:
    return state_dir / MONITORING_DIR


def private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.stat()
    if info.st_uid != os.geteuid() or info.st_mode & 0o022:
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


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None
