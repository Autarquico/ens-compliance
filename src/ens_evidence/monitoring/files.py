"""Ficheros de `state/monitoring`: escritura atómica, privados, un escritor por fichero."""

import json
from pathlib import Path
from typing import Any

from ens_evidence.workspace.state_files import private_dir, write_json

__all__ = ["monitoring_dir", "private_dir", "read_json", "write_json"]

MONITORING_DIR = "monitoring"


def monitoring_dir(state_dir: Path) -> Path:
    return state_dir / MONITORING_DIR


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None
