"""Derivados del expediente: `dossier.md` y las vistas guardadas con `show --save`.

Ninguno es fuente de verdad: cada uno se puede regenerar de `dossier.json` y la cadena. Por eso se
contrastan byte a byte con lo que saldría de regenerarlo; si alguien lo edita a mano, el expediente
no se verifica.
"""

import hashlib
import re
from collections.abc import Sequence

from ens_evidence.assessment.dossier.model import DossierSnapshot
from ens_evidence.assessment.dossier.render import render_snapshot_md, render_view_md
from ens_evidence.assessment.dossier.store import ASSESSMENTS_PREFIX, DOSSIER_ID, MARKDOWN_NAME
from ens_evidence.assessment.dossier.view import DossierView, TransitionEntry
from ens_evidence.workspace import AlreadyExists, NotFound, WorkspaceStore

VIEW_DIR = "view"
_VIEW_NAME = re.compile(r"(\d{6})-([0-9a-f]{12})\.(md|json)")


def render_view(snapshot: DossierSnapshot, chain: Sequence[TransitionEntry], fmt: str) -> str:
    if fmt == "json":
        view = DossierView(snapshot=snapshot, transitions=list(chain))
        return view.model_dump_json(indent=2) + "\n"
    return render_view_md(snapshot, list(chain))


def save_view(
    store: WorkspaceStore,
    dossier: str,
    snapshot: DossierSnapshot,
    chain: Sequence[TransitionEntry],
    fmt: str,
) -> tuple[str, bool]:
    """Guarda la vista de la cadena entera; devuelve (clave, si es nueva). Solo adición."""
    text = render_view(snapshot, chain, fmt)
    digest = hashlib.sha256(text.encode()).hexdigest()[:12]
    key = f"{ASSESSMENTS_PREFIX}/{dossier}/{VIEW_DIR}/{len(chain):06d}-{digest}.{fmt}"
    try:
        store.create_bytes(key, text.encode())
    except AlreadyExists:
        return key, False
    return key, True


def has_markdown(store: WorkspaceStore, dossier: str) -> bool:
    return store.exists(f"{ASSESSMENTS_PREFIX}/{dossier}/{MARKDOWN_NAME}")


def markdown_problems(store: WorkspaceStore, dossier: str, snapshot: DossierSnapshot) -> list[str]:
    """`dossier.md` es lo que lee el auditor: si existe, tiene que ser el que sale del JSON."""
    key = f"{ASSESSMENTS_PREFIX}/{dossier}/{MARKDOWN_NAME}"
    try:
        stored = store.read_bytes(key)
    except NotFound:
        return []
    if stored != render_snapshot_md(snapshot).encode("utf-8"):
        return [f"{key}: no coincide con dossier.json; restáuralo desde la copia o regenéralo"]
    return []


def view_problems(
    store: WorkspaceStore,
    dossier: str,
    snapshot: DossierSnapshot,
    chain: Sequence[TransitionEntry],
) -> list[str]:
    """Cada vista guardada tiene que ser la que se obtiene de la cadena hasta su longitud."""
    prefix = f"{ASSESSMENTS_PREFIX}/{dossier}/{VIEW_DIR}"
    problems: list[str] = []
    for key in sorted(store.list(prefix)):
        found = _VIEW_NAME.fullmatch(key.removeprefix(prefix + "/"))
        if found is None or int(found[1]) > len(chain):
            problems.append(f"{key}: vista ajena a este expediente")
            continue
        expected = render_view(snapshot, chain[: int(found[1])], found[3]).encode("utf-8")
        stored = store.read_bytes(key)
        if stored != expected or hashlib.sha256(stored).hexdigest()[:12] != found[2]:
            problems.append(f"{key}: la vista no coincide con la que sale del expediente")
    return problems


def dossier_exists(store: WorkspaceStore, dossier: str) -> bool:
    """Hay algún fichero bajo el id; un id con errata no tiene ninguno."""
    return bool(DOSSIER_ID.fullmatch(dossier)) and bool(
        store.list(f"{ASSESSMENTS_PREFIX}/{dossier}")
    )
