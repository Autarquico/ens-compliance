"""Estado de un expediente: estado reconstruido, deriva, integridad, huellas y siguiente paso.

Solo lee. El siguiente paso nombra la persona que debe actuar; nunca propone declarar en
MEDIA/ALTA, donde la conformidad exige auditoría de certificación (art. 38.1).
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from ens_evidence.assessment.dossier.derived import (
    derived_problems,
    dossier_exists,
)
from ens_evidence.assessment.dossier.drift import DriftReport, rebuild_and_diff
from ens_evidence.assessment.dossier.model import (
    DossierSnapshot,
    SectionState,
    snapshot_readiness,
)
from ens_evidence.assessment.dossier.notices import NOT_READY_NOTICE
from ens_evidence.assessment.dossier.transitions import (
    CERTIFICATION_NOTE,
    TransitionScan,
    evaluate_for_declaration,
    scan_transitions,
)
from ens_evidence.checks.source import StoredCheckResults
from ens_evidence.matrix import CheckResultsSource
from ens_evidence.matrix.messages import role_label
from ens_evidence.models.enums import Category, DossierState, Readiness, Role
from ens_evidence.workspace import WorkspaceStore

RECOVER = "recupera el expediente desde la copia (runbook); no admite más transiciones"
RECOVER_SNAPSHOT = (
    "restaura dossier.json desde la copia (runbook) o genera un expediente nuevo; "
    "no admite más transiciones"
)
RESTORE_DERIVED = (
    "restaura desde la copia (runbook) el fichero indicado y comprueba con `dossier verify`"
)
UNKNOWN = "No hay ningún expediente con ese id; consulta `assessment dossier list`."
RESOLVE = "resuélvelos y vuelve a intentarlo"
REGENERATE = "genera un expediente nuevo: este ya no describe el estado actual"
NEW_DOSSIER = "genera un expediente nuevo cuando el sistema esté preparado"
PUBLICITY = (
    "ninguno por la herramienta: la declaración ya está registrada; la organización debe darle "
    "publicidad (art. 38.2 del RD 311/2022, según la ITS aplicable)"
)


def next_step(
    category: Category,
    state: DossierState,
    dossier: str,
    incomplete: tuple[str, ...] = (),
    *,
    readiness_blocked: bool = False,
) -> str:
    """Quién da el siguiente paso y con qué comando. Única fuente para `status` y los comandos."""
    basic = category is Category.BASICA
    match state:
        case DossierState.GENERATED:
            step = (
                f"autoevaluación: {role_label(Role.SYSTEM_ADMINISTRATOR)} (o su delegado) revisa "
                f"cada medida y registra `assessment dossier self-assess {dossier} --by <nombre> "
                "--role system_administrator`"
            )
        case DossierState.SELF_ASSESSED:
            step = (
                f"análisis del {role_label(Role.SECURITY_OFFICER)}: registra "
                f"`assessment dossier analyze {dossier}` y eleva las conclusiones al "
                f"{role_label(Role.SYSTEM_OWNER)}"
            )
        case DossierState.ANALYZED if basic and readiness_blocked:
            return f"ninguno en este expediente: {NEW_DOSSIER}"
        case DossierState.ANALYZED if basic:
            pending = (
                f"; hay secciones incompletas ({'; '.join(incomplete)}): complétalas en un "
                "expediente nuevo o reconócelas con `--acknowledge-incomplete`"
                if incomplete
                else ""
            )
            return (
                f"la {role_label(Role.MANAGEMENT)} decide y, si la organización declara, "
                f"registra `assessment dossier declare {dossier} --by <nombre>`{pending}; la "
                "herramienta solo registra el acto"
            )
        case DossierState.ANALYZED:
            return f"ninguno: {CERTIFICATION_NOTE}"
        case _:
            return PUBLICITY
    return step


def snapshot_readiness_blocked(snapshot: DossierSnapshot) -> bool:
    """BÁSICA y la instantánea no estaba READY: nunca se podrá declarar con este expediente."""
    if snapshot.category is not Category.BASICA:
        return False
    return snapshot_readiness(snapshot) is not Readiness.READY


def incomplete_sections(snapshot: DossierSnapshot) -> tuple[str, ...]:
    return tuple(
        f"{section.number}. {section.title}"
        for section in snapshot.sections
        if section.state is SectionState.INCOMPLETE
    )


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
    known: bool
    """False si no existe ningún fichero con ese id (errata), a diferencia de uno dañado."""
    category: Category | None
    state: DossierState | None
    """None si la cadena no se verifica: el estado no es fiable."""
    notice: str | None
    integrity: tuple[str, ...]
    drift: DriftReport | None
    """None si la instantánea no se puede leer."""
    declaration_blocked: tuple[str, ...]
    """Por qué no se puede declarar ahora (solo en BÁSICA, ANALYZED)."""
    incomplete_sections: tuple[str, ...]
    readiness_notice: str | None
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
    drift = rebuild_and_diff(store, scan.snapshot, checks=checks) if scan.snapshot else None
    state = scan.dossier.state if scan.dossier else None
    category = scan.snapshot.category if scan.snapshot else None
    analysis = next((e for e in scan.entries if e.record.to_state is DossierState.ANALYZED), None)
    declared = next((e for e in scan.entries if e.record.to_state is DossierState.DECLARED), None)
    derived = _derived_problems(store, scan)
    blocked, needs_new = _declaration_blocks(store, scan, state, drift, now, checks)
    snapshot = scan.snapshot
    return DossierStatus(
        dossier_id=dossier,
        known=snapshot is not None or dossier_exists(store, dossier),
        category=category,
        state=state,
        notice=snapshot.notice if snapshot else None,
        integrity=(*scan.problems, *derived),
        drift=drift,
        declaration_blocked=blocked,
        incomplete_sections=incomplete_sections(snapshot) if snapshot else (),
        readiness_notice=(
            NOT_READY_NOTICE if snapshot and snapshot_readiness_blocked(snapshot) else None
        ),
        next_step=_next_step(
            dossier,
            snapshot,
            state,
            broken=bool(scan.problems),
            derived=bool(derived),
            drift=drift,
            blocked=blocked,
            needs_new=needs_new,
        ),
        fingerprints=Fingerprints(
            snapshot=scan.snapshot_sha256,
            analysis=analysis.sha256 if analysis else None,
            cited_analysis=declared.record.analysis_sha256 if declared else None,
            last_transition=scan.entries[-1].sha256 if scan.entries else None,
        ),
    )


def _derived_problems(store: WorkspaceStore, scan: TransitionScan) -> list[str]:
    if scan.snapshot is None:
        return []
    chain = None if scan.problems else list(scan.entries)
    return derived_problems(store, scan.dossier_id, scan.snapshot, chain)


def _declaration_blocks(
    store: WorkspaceStore,
    scan: TransitionScan,
    state: DossierState | None,
    drift: DriftReport | None,
    now: datetime | None,
    source: CheckResultsSource | None,
) -> tuple[tuple[str, ...], bool]:
    """Lo que impide declarar: (motivos, si solo se arregla con un expediente nuevo).

    Cubre lo mismo que exige `record_transition` al declarar: instantánea READY, evaluación actual
    READY (`analyze` ya exige la revisión del juicio manual en BÁSICA)."""
    snapshot = scan.snapshot
    if (
        snapshot is None
        or snapshot.category is not Category.BASICA
        or state is not DossierState.ANALYZED
        or drift is None
        or not drift.current
    ):
        return (), False
    reasons: list[str] = []
    needs_new = snapshot_readiness_blocked(snapshot)
    if needs_new:
        reasons.append("el sistema no estaba preparado al generar este expediente")
    instant = now or datetime.now(UTC)
    _, problems = evaluate_for_declaration(
        store, instant, source or StoredCheckResults(reference_now=instant)
    )
    reasons += problems
    return tuple(reasons), needs_new


def _next_step(
    dossier: str,
    snapshot: DossierSnapshot | None,
    state: DossierState | None,
    *,
    broken: bool,
    derived: bool,
    drift: DriftReport | None,
    blocked: tuple[str, ...],
    needs_new: bool,
) -> str:
    if snapshot is None:
        return RECOVER_SNAPSHOT
    if broken or state is None:
        return RECOVER
    if derived:
        return RESTORE_DERIVED
    if blocked:
        reasons = "; ".join(blocked)
        return f"no se puede declarar: {reasons}; {NEW_DOSSIER if needs_new else RESOLVE}"
    if state is DossierState.DECLARED:
        return next_step(snapshot.category, state, dossier, incomplete_sections(snapshot))
    if drift is not None and not drift.current:
        return REGENERATE
    return next_step(snapshot.category, state, dossier, incomplete_sections(snapshot))
