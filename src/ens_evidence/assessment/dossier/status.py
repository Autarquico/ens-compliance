"""Estado de un expediente: estado reconstruido, deriva, integridad, huellas y siguiente paso.

Solo lee. El siguiente paso nombra la persona que debe actuar; nunca propone declarar en
MEDIA/ALTA, donde la conformidad exige auditoría de certificación (art. 38.1).
"""

from dataclasses import dataclass
from datetime import datetime

from ens_evidence.assessment.dossier.drift import DriftReport, rebuild_and_diff
from ens_evidence.assessment.dossier.model import INTERNAL_REVIEW_NOTICE
from ens_evidence.assessment.dossier.transitions import CERTIFICATION_NOTE, scan_transitions
from ens_evidence.checks.source import StoredCheckResults
from ens_evidence.matrix import CheckResultsSource
from ens_evidence.matrix.messages import role_label
from ens_evidence.models.enums import Category, DossierState, Role
from ens_evidence.workspace import WorkspaceStore

RECOVER = "recupera el expediente desde la copia (runbook); no admite más transiciones"
REGENERATE = "genera un expediente nuevo: este ya no describe el estado actual"
_STEPS = {
    DossierState.GENERATED: (
        f"autoevaluación ({role_label(Role.SYSTEM_ADMINISTRATOR)} o su delegado), "
        "con una revisión en cada fila"
    ),
    DossierState.SELF_ASSESSED: (
        f"análisis del {role_label(Role.SECURITY_OFFICER)}, que eleva las conclusiones al "
        f"{role_label(Role.SYSTEM_OWNER)}"
    ),
    DossierState.ANALYZED: (
        f"declaración de conformidad: acto de la {role_label(Role.MANAGEMENT)}, que la "
        "herramienta solo registra"
    ),
    DossierState.DECLARED: "ninguno: la declaración ya está registrada",
}


@dataclass(frozen=True)
class Fingerprints:
    snapshot: str | None
    analysis: str | None
    """Huella del registro ANALYZED (la que cita la declaración)."""
    cited_analysis: str | None
    """Huella del análisis que cita DECLARED, si hay declaración."""
    last_transition: str | None


@dataclass(frozen=True)
class DossierStatus:
    dossier_id: str
    category: Category | None
    state: DossierState | None
    """None si la cadena no se verifica: el estado no es fiable."""
    notice: str | None
    integrity: tuple[str, ...]
    drift: DriftReport | None
    """None si la instantánea no se puede leer."""
    next_step: str
    fingerprints: Fingerprints


def dossier_status(
    store: WorkspaceStore,
    dossier: str,
    *,
    now: datetime | None = None,
    checks: CheckResultsSource | None = None,
) -> DossierStatus:
    scan = scan_transitions(store, dossier)
    source = checks or (StoredCheckResults(reference_now=now) if now else None)
    drift = rebuild_and_diff(store, scan.snapshot, checks=source) if scan.snapshot else None
    state = scan.dossier.state if scan.dossier else None
    category = scan.snapshot.category if scan.snapshot else None
    analysis = next((e for e in scan.entries if e.record.to_state is DossierState.ANALYZED), None)
    declared = next((e for e in scan.entries if e.record.to_state is DossierState.DECLARED), None)
    return DossierStatus(
        dossier_id=dossier,
        category=category,
        state=state,
        notice=scan.snapshot.notice if scan.snapshot else None,
        integrity=scan.problems,
        drift=drift,
        next_step=_next_step(category, state, bool(scan.problems), drift),
        fingerprints=Fingerprints(
            snapshot=scan.snapshot_sha256,
            analysis=analysis.sha256 if analysis else None,
            cited_analysis=declared.record.analysis_sha256 if declared else None,
            last_transition=scan.entries[-1].sha256 if scan.entries else None,
        ),
    )


def _next_step(
    category: Category | None,
    state: DossierState | None,
    broken: bool,
    drift: DriftReport | None,
) -> str:
    if broken or state is None or category is None:
        return RECOVER
    if state is DossierState.DECLARED:
        return _STEPS[state]
    if drift is not None and not drift.current:
        return REGENERATE
    if category is not Category.BASICA and state is DossierState.ANALYZED:
        return f"ninguno: {CERTIFICATION_NOTE}"
    step = _STEPS[state]
    return step if category is Category.BASICA else f"{step} ({INTERNAL_REVIEW_NOTICE})"
