"""Almacenamiento de solo adición del expediente: `evidence/assessments/<id>/dossier.json`.

El id es `<instante>-<sha12>`: el instante de generación y la huella de los bytes. Al leer se
exige que ambos coincidan con el contenido, y se vuelve a pasar la política de privacidad, porque
un fichero editado a mano tras generarlo no debe llegar a una auditoría ni a un LLM.
Los errores nombran rutas y claves, nunca valores.
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from ens_evidence.assessment.dossier.model import DossierSnapshot
from ens_evidence.assessment.dossier.privacy import export_problems, secret_problems
from ens_evidence.assessment.dossier.render import render_snapshot_json
from ens_evidence.dossier_layout import (
    ASSESSMENTS_PREFIX,
    DOSSIER_ID,
    SNAPSHOT_NAME,
    matches_content,
)
from ens_evidence.matrix.store import instant_stamp
from ens_evidence.workspace import AlreadyExists, NotFound, NotText, WorkspaceStore

MARKDOWN_NAME = "dossier.md"


class DossierUnreadable(Exception):
    def __init__(self, dossier: str, problems: list[str]):
        self.dossier = dossier
        self.problems = problems
        super().__init__(f"{dossier}: " + "; ".join(problems))


@dataclass(frozen=True)
class StoredDossier:
    id: str
    snapshot: DossierSnapshot | None
    problems: list[str]


def dossier_id(snapshot_bytes: bytes, generated_at) -> str:
    return f"{instant_stamp(generated_at)}-{hashlib.sha256(snapshot_bytes).hexdigest()[:12]}"


def _key(dossier: str, name: str) -> str:
    return f"{ASSESSMENTS_PREFIX}/{dossier}/{name}"


def save_snapshot(store: WorkspaceStore, snapshot: DossierSnapshot) -> str:
    """Crea `dossier.json` en exclusiva (`AlreadyExists` si ya está) y devuelve el id."""
    data = render_snapshot_json(snapshot)
    dossier = dossier_id(data, snapshot.inputs.generated_at)
    store.create_bytes(_key(dossier, SNAPSHOT_NAME), data)
    return dossier


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError("clave duplicada")
    return dict(pairs)


def _verified(store: WorkspaceStore, dossier: str) -> tuple[DossierSnapshot, bytes]:
    if not DOSSIER_ID.fullmatch(dossier):
        raise DossierUnreadable(dossier, ["el id no tiene la forma <instante>-<sha12>"])
    key = _key(dossier, SNAPSHOT_NAME)
    try:
        data = store.read_bytes(key)
    except NotFound:
        raise DossierUnreadable(dossier, [f"{key}: no existe"]) from None
    if not matches_content(dossier, data):
        raise DossierUnreadable(dossier, [f"{key}: el contenido no coincide con la huella del id"])
    try:
        raw = json.loads(data.decode("utf-8"), object_pairs_hook=_no_duplicates)
        snapshot = DossierSnapshot.model_validate(raw)
    except (UnicodeDecodeError, ValueError, ValidationError) as error:
        detail = (
            [f"{key}.{'.'.join(map(str, e['loc']))}: {e['type']}" for e in error.errors()]
            if isinstance(error, ValidationError)
            else [f"{key}: no se lee de forma estricta ({type(error).__name__})"]
        )
        raise DossierUnreadable(dossier, detail) from None
    if not dossier.startswith(instant_stamp(snapshot.inputs.generated_at) + "-"):
        raise DossierUnreadable(dossier, [f"{key}: el instante del id no es el de generación"])
    leaks = _privacy_problems(snapshot)
    if leaks:
        raise DossierUnreadable(dossier, leaks)
    return snapshot, data


def _privacy_problems(snapshot: DossierSnapshot) -> list[str]:
    document = snapshot.model_dump(mode="json")
    found = secret_problems(document)
    for section in document["sections"]:
        where = f"sección {section['number']} ({section['title']}): "
        found += export_problems(where, section["content"])
    return sorted(set(found))


def load_snapshot(store: WorkspaceStore, dossier: str) -> DossierSnapshot:
    return _verified(store, dossier)[0]


def stored_snapshot_sha256(store: WorkspaceStore, dossier: str) -> str:
    """Huella de `dossier.json`, solo si el expediente se lee y verifica."""
    return hashlib.sha256(_verified(store, dossier)[1]).hexdigest()


def list_dossiers(store: WorkspaceStore) -> list[StoredDossier]:
    prefix = ASSESSMENTS_PREFIX + "/"
    ids = sorted({k[len(prefix) :].split("/", 1)[0] for k in store.list(ASSESSMENTS_PREFIX)})
    found: list[StoredDossier] = []
    for dossier in ids:
        try:
            found.append(StoredDossier(dossier, load_snapshot(store, dossier), []))
        except DossierUnreadable as error:
            found.append(StoredDossier(dossier, None, error.problems))
    return found


def save_markdown(store: WorkspaceStore, dossier: str, markdown: str) -> None:
    """Escribe `dossier.md` si no existe; `AlreadyExists` si ya está. Exige expediente legible."""
    load_snapshot(store, dossier)
    store.create_bytes(_key(dossier, MARKDOWN_NAME), markdown.encode("utf-8"))


__all__ = [
    "ASSESSMENTS_PREFIX",
    "DOSSIER_ID",
    "SNAPSHOT_NAME",
    "AlreadyExists",
    "DossierUnreadable",
    "NotText",
    "StoredDossier",
    "dossier_id",
    "list_dossiers",
    "load_snapshot",
    "save_markdown",
    "save_snapshot",
    "stored_snapshot_sha256",
]
