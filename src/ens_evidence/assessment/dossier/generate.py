"""Generar el expediente con el estado actual del workspace: lo comparten la CLI y la web."""

from dataclasses import dataclass
from datetime import datetime

from ens_evidence.assessment.dossier.build import build_snapshot, current_inputs
from ens_evidence.assessment.dossier.model import DossierSnapshot
from ens_evidence.assessment.dossier.package import PackageRejected, save_package
from ens_evidence.assessment.dossier.render import render_snapshot_md
from ens_evidence.assessment.dossier.store import save_markdown, save_snapshot
from ens_evidence.checks.source import StoredCheckResults
from ens_evidence.models.enums import Category
from ens_evidence.workspace import WorkspaceStore


@dataclass(frozen=True)
class GeneratedDossier:
    dossier: str
    snapshot: DossierSnapshot
    package_errors: tuple[str, ...] = ()


def generate_dossier(store: WorkspaceStore, now: datetime) -> GeneratedDossier:
    """Instantánea, dossier.md y, si no es BÁSICA, el paquete por medida.

    Propaga los rechazos de `build_snapshot` y `AlreadyExists`; un paquete rechazado no es
    excepción: el expediente ya está guardado y se devuelve con sus errores.
    """
    snapshot = build_snapshot(
        store, current_inputs(now), checks=StoredCheckResults(reference_now=now)
    )
    dossier = save_snapshot(store, snapshot)
    save_markdown(store, dossier, render_snapshot_md(snapshot))
    if snapshot.category is Category.BASICA:
        return GeneratedDossier(dossier, snapshot)
    try:
        save_package(store, dossier, snapshot)
    except PackageRejected as exc:
        return GeneratedDossier(dossier, snapshot, tuple(exc.errors))
    return GeneratedDossier(dossier, snapshot)
