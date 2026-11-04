"""Dónde vive un expediente: `evidence/assessments/<instante>-<sha12>/dossier.json`.

Módulo hoja: el expediente y la pre-auditoría lo leen sin importarse entre sí.
"""

import hashlib
import re
from datetime import datetime

from ens_evidence.chain import instant_stamp
from ens_evidence.workspace import WorkspaceStore

ASSESSMENTS_PREFIX = "evidence/assessments"
SNAPSHOT_NAME = "dossier.json"

DOSSIER_ID = re.compile(r"\d{8}T\d{12}Z-[0-9a-f]{12}")


def snapshot_key(dossier: str) -> str:
    return f"{ASSESSMENTS_PREFIX}/{dossier}/{SNAPSHOT_NAME}"


def matches_content(dossier: str, data: bytes) -> bool:
    """El id termina en la huella de los bytes de `dossier.json`."""
    return dossier.endswith(hashlib.sha256(data).hexdigest()[:12])


def dossiers_before(store: WorkspaceStore, at: datetime) -> list[str]:
    """Los ids de expediente generados antes de `at` (por el instante de su id), en orden."""
    stamp = instant_stamp(at)
    ids = {
        key.removeprefix(f"{ASSESSMENTS_PREFIX}/").split("/", 1)[0]
        for key in store.list(ASSESSMENTS_PREFIX)
    }
    return sorted(d for d in ids if DOSSIER_ID.fullmatch(d) and d.split("-", 1)[0] < stamp)
