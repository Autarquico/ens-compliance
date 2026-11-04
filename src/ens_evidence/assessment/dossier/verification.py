"""Lo que comprueba `assessment dossier verify`: reproducción con las entradas congeladas,
pre-auditorías congeladas, derivados, cadena de transiciones y paquete. Solo lee."""

from ens_evidence.assessment.dossier.build import DossierRejected, build_snapshot
from ens_evidence.assessment.dossier.derived import markdown_problems, view_problems
from ens_evidence.assessment.dossier.model import DossierSnapshot
from ens_evidence.assessment.dossier.package import has_package, verify_package
from ens_evidence.assessment.dossier.render import snapshot_sha256
from ens_evidence.assessment.dossier.store import stored_snapshot_sha256
from ens_evidence.assessment.dossier.transitions import scan_transitions
from ens_evidence.categorization import CategorizationNotCurrent
from ens_evidence.models.enums import Category
from ens_evidence.preaudit.findings import missing_frozen
from ens_evidence.workspace import WorkspaceStore


def package_required(store: WorkspaceStore, dossier: str, snapshot: DossierSnapshot) -> bool:
    """El paquete se verifica si existe y, en MEDIA/ALTA, siempre (allí es obligatorio)."""
    return has_package(store, dossier) or snapshot.category is not Category.BASICA


def verification_problems(
    store: WorkspaceStore, dossier: str, snapshot: DossierSnapshot
) -> list[str]:
    """Vacío si el expediente se reproduce byte a byte, sigue ahí la pre-auditoría que congeló
    y sus derivados, su cadena y su paquete se verifican."""
    frozen = [
        *(snapshot.inputs.preaudit_runs or []),
        *(snapshot.inputs.preaudit_dispositions or []),
    ]
    problems = [f"pre-auditoría: {p}" for p in missing_frozen(store, dossier, frozen)]
    try:
        rebuilt = build_snapshot(store, snapshot.inputs)
    except (DossierRejected, CategorizationNotCurrent) as exc:
        problems.append(f"no se puede reproducir con las entradas congeladas ({exc})")
    else:
        if snapshot_sha256(rebuilt) != stored_snapshot_sha256(store, dossier):
            problems.append("la reproducción con las entradas congeladas no da los mismos bytes")
    problems += markdown_problems(store, dossier, snapshot)
    scan = scan_transitions(store, dossier)
    problems += scan.problems
    if not scan.problems:
        problems += view_problems(store, dossier, snapshot, list(scan.entries))
    if package_required(store, dossier, snapshot):
        problems += verify_package(store, dossier).problems
    return problems
