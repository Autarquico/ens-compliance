"""Cadena de transiciones del expediente, de solo adición:
`evidence/assessments/<id>/transitions/<n>-<instante>-<sha12>.yaml`.

Orden de escritura, que fija qué deja un fallo a medias:

1. todas las comprobaciones (integridad, tiempo, deriva, categoría, precondiciones, modelo): un
   rechazo no escribe;
2. reservar `transitions/seq/<n>` con creación exclusiva: si existe, otra orden ganó;
3. escribir el registro.

Un fallo entre 2 y 3 deja una reserva sin registro: `scan_transitions` lo marca roto y el
expediente no admite más transiciones hasta recuperarlo desde la copia (runbook).
La herramienta solo registra el acto humano; los nombres son declarados, no autenticados.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from pydantic import ValidationError

from ens_evidence import chain
from ens_evidence.assessment.dossier.derived import derived_problems
from ens_evidence.assessment.dossier.drift import rebuild_and_diff
from ens_evidence.assessment.dossier.model import (
    DossierSnapshot,
    SectionState,
    snapshot_export,
    snapshot_readiness,
)
from ens_evidence.assessment.dossier.notices import CERTIFICATION_NOTE
from ens_evidence.assessment.dossier.privacy import free_text_problems
from ens_evidence.assessment.dossier.store import (
    ASSESSMENTS_PREFIX,
    DossierUnreadable,
    load_snapshot,
    stored_snapshot_sha256,
)
from ens_evidence.assessment.dossier.view import TransitionEntry, TransitionRecord
from ens_evidence.categorization import CategorizationNotCurrent
from ens_evidence.categorization.dossier import DossierCategoryMismatch, require_dossier_category
from ens_evidence.checks.source import StoredCheckResults
from ens_evidence.evidence.verify import verify_one
from ens_evidence.matrix import CheckResultsSource, assess
from ens_evidence.matrix.evaluate import AssessmentResult
from ens_evidence.matrix.messages import KIND_FALLBACK
from ens_evidence.models.dossier import (
    ORDER,
    SELF_ASSESSORS,
    CorrectiveMeasure,
    Dossier,
    Transition,
    record_sha256,
    transition,
)
from ens_evidence.models.enums import Category, DossierState, Readiness, Role
from ens_evidence.models.readiness import ReadinessResult
from ens_evidence.orgdocs import DocumentApprovalRejected, declared_signatory, document_status
from ens_evidence.privacy_text import check_declared_name
from ens_evidence.scope.messages import describe_validation
from ens_evidence.workspace import WorkspaceStore

DESIGNATION_DOCUMENT = "designacion-responsables"
TRANSITIONS_DIR = "transitions"
_SHOWN_ROWS = 10

_STEP = {
    DossierState.SELF_ASSESSED: "la autoevaluación del administrador del sistema o su delegado",
    DossierState.ANALYZED: "el análisis del responsable de la seguridad",
    DossierState.DECLARED: "la declaración de la dirección",
}
_EXPECTED_ROLE = {
    DossierState.ANALYZED: Role.SECURITY_OFFICER,
    DossierState.DECLARED: Role.MANAGEMENT,
}
MANUAL_REVIEW_REQUIRED = (
    "--manual-judgment-review: en BÁSICA el análisis exige confirmar la revisión humana del "
    "juicio; sin ella no se podrá declarar y el expediente habría que generarlo de nuevo"
)


class TransitionRejected(Exception):
    """No se escribe nada; `errors` dice por qué (rutas y tipos; la deriva muestra valores cortos
    que ya pasaron la política de exportación)."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


_Stored = chain.Stored[TransitionRecord]


@dataclass(frozen=True)
class TransitionScan:
    """Cadena leída y verificada. Con `problems` el expediente está roto: `dossier` es None."""

    dossier_id: str
    snapshot: DossierSnapshot | None
    snapshot_sha256: str | None
    entries: tuple[TransitionEntry, ...]
    problems: tuple[str, ...]
    dossier: Dossier | None
    last_file_sha256: str | None


@dataclass(frozen=True)
class Recorded:
    entry: TransitionEntry
    dossier: Dossier
    notice: str | None
    """Etiqueta de revisión interna (MEDIA/ALTA); None en BÁSICA."""


# --- claves ------------------------------------------------------------------------------------


def _prefix(dossier: str) -> str:
    return f"{ASSESSMENTS_PREFIX}/{dossier}/{TRANSITIONS_DIR}"


def seq_key(dossier: str, seq: int) -> str:
    return chain.seq_key(_prefix(dossier), seq)


def record_key(record: TransitionRecord, data: bytes) -> str:
    return chain.record_key(_prefix(record.dossier_id), record.seq, record.recorded_at, data)


# --- integridad --------------------------------------------------------------------------------


def scan_transitions(store: WorkspaceStore, dossier: str) -> TransitionScan:
    """Integridad completa: reservas y registros se corresponden uno a uno, sin huecos,
    duplicados ni claves ajenas; cada registro encadena con el anterior, lleva la huella del
    `dossier.json` y su clave coincide con su contenido. Todo problema bloquea."""
    try:
        snapshot = load_snapshot(store, dossier)
        snapshot_sha = stored_snapshot_sha256(store, dossier)
    except DossierUnreadable as exc:
        return TransitionScan(dossier, None, None, (), tuple(exc.problems), None, None)
    stored, reserved, problems = chain.read_chain(
        store,
        _prefix(dossier),
        TransitionRecord,
        owner=dossier,
        owner_of=lambda record: record.dossier_id,
        owner_label="expediente",
        chain_label="las transiciones",
    )
    by_seq = chain.group_by_seq(stored)
    pairs = chain.linked_pairs(stored, by_seq)
    problems += chain.sequence_problems(by_seq, reserved)
    problems += chain.link_problems(pairs)
    problems += _record_problems(stored, pairs, snapshot, snapshot_sha)
    entries = tuple(_entry(item) for item in stored)
    dossier_state = None if problems else _reconstruct(snapshot, stored, problems)
    return TransitionScan(
        dossier,
        snapshot,
        snapshot_sha,
        entries,
        tuple(problems),
        dossier_state,
        stored[-1].sha256 if stored else None,
    )


def _entry(item: _Stored) -> TransitionEntry:
    return TransitionEntry(
        key=item.key,
        sha256=record_sha256(item.record.transition),
        recorded_at=item.record.recorded_at,
        record=item.record.transition,
    )


def entries(store: WorkspaceStore, dossier: str) -> list[TransitionEntry]:
    """Las transiciones para la vista; `TransitionRejected` si la cadena no se verifica."""
    scan = scan_transitions(store, dossier)
    if scan.problems:
        raise TransitionRejected([f"{dossier} está roto", *scan.problems])
    return list(scan.entries)


def _record_problems(
    stored: list[_Stored],
    pairs: list[tuple[_Stored, _Stored]],
    snapshot: DossierSnapshot,
    snapshot_sha: str,
) -> list[str]:
    problems: list[str] = []
    for item in stored:
        record = item.record
        if record.transition.dossier_sha256 != snapshot_sha:
            problems.append(f"{item.key}: lleva la huella de otro dossier.json")
        if record.transition.at < snapshot.inputs.generated_at:
            problems.append(f"{item.key}: at es anterior a la generación del expediente")
        if record.transition.at > record.recorded_at:
            problems.append(f"{item.key}: at es posterior a recorded_at")
        problems += [f"{item.key}: {p}" for p in _free_text_problems(record.transition)]
    problems += [
        f"registros {a.record.seq} y {b.record.seq}: recorded_at retrocede"
        for a, b in pairs
        if b.record.recorded_at < a.record.recorded_at
    ]
    return problems


def _free_text_problems(record: Transition) -> list[str]:
    """Los mismos detectores que al escribir, también al leer: un registro forjado a mano con
    las huellas recalculadas no llega a `show` ni a la pre-auditoría con secretos o datos."""
    problems = check_declared_name(record.actor, "actor")
    if record.elevated_to:
        problems += check_declared_name(record.elevated_to, "elevated_to")
    if record.conclusions:
        problems += free_text_problems("conclusiones", record.conclusions)
    for index, measure in enumerate(record.corrective_measures):
        problems += free_text_problems(f"medidas correctoras[{index}]", measure.description)
    return problems


def _reconstruct(
    snapshot: DossierSnapshot, stored: list[_Stored], problems: list[str]
) -> Dossier | None:
    history = [item.record.transition for item in stored]
    state = history[-1].to_state if history else DossierState.GENERATED
    try:
        dossier = Dossier(category=snapshot.category, state=state, transitions=history)
    except ValidationError as exc:
        problems += [
            f"transiciones: {error['msg'].removeprefix('Value error, ')}"
            for error in exc.errors(include_url=False)
        ]
        return None
    declared = next((t for t in history if t.to_state is DossierState.DECLARED), None)
    if declared is None:
        return dossier
    if snapshot_readiness(snapshot) is not Readiness.READY:
        problems.append("DECLARED sobre un expediente generado sin estar el sistema preparado")
    if declared.acknowledged_incomplete != _incomplete(snapshot):
        problems.append(
            "DECLARED no reconoce exactamente las secciones INCOMPLETE de la instantánea"
        )
    return None if problems else dossier


def _incomplete(snapshot: DossierSnapshot) -> list[int]:
    return [s.number for s in snapshot.sections if s.state is SectionState.INCOMPLETE]


# --- registro ----------------------------------------------------------------------------------


def record_transition(
    store: WorkspaceStore,
    dossier: str,
    to_state: DossierState,
    *,
    by: str,
    role: Role,
    at: datetime,
    now: datetime,
    checks: CheckResultsSource | None = None,
    conclusions: str | None = None,
    corrective_measures: Sequence[CorrectiveMeasure] = (),
    manual_judgment_review: bool = False,
    elevated_to: str | None = None,
    elevation_confirmed: bool = False,
    declaration_evidence_id: str | None = None,
    acknowledge_incomplete: bool = False,
) -> Recorded:
    """Añade una transición. `TransitionRejected` si algo falla: no se escribe nada.

    `at` es cuándo la persona hizo el acto; `now`, cuándo se registra. La preparación (READY) se
    evalúa aquí, nunca la aporta el llamador. Orden: primero la integridad (la cadena y los
    derivados: `dossier.md`, el paquete y las vistas), luego la deriva (si el expediente ya no
    describe el estado actual, ese es el motivo, sea cual sea la persona o el estado) y después
    las comprobaciones de la transición."""
    scan = scan_transitions(store, dossier)
    if scan.problems or scan.snapshot is None or scan.dossier is None:
        raise TransitionRejected(
            [f"{dossier} está roto y no admite más transiciones", *scan.problems]
        )
    snapshot, current = scan.snapshot, scan.dossier
    previous = scan.entries[-1] if scan.entries else None

    altered = derived_problems(store, dossier, snapshot, list(scan.entries))
    if altered:
        raise TransitionRejected(
            [
                f"{dossier} tiene ficheros derivados alterados a mano y no admite más "
                "transiciones; restaura lo indicado desde la copia (runbook) y comprueba con "
                "`assessment dossier verify`",
                *altered,
            ]
        )
    drift = rebuild_and_diff(store, snapshot, checks=checks).drift
    if drift:
        raise TransitionRejected(
            [
                "el expediente ya no describe el estado actual; genera uno nuevo",
                *(f"{section}: {line}" for section, lines in drift.items() for line in lines),
            ]
        )
    _check_time(snapshot, previous, at, now)
    resolved = _preconditions(
        store,
        scan,
        to_state,
        by=by,
        role=role,
        conclusions=conclusions,
        corrective_measures=corrective_measures,
        manual_judgment_review=manual_judgment_review,
        elevated_to=elevated_to,
        elevation_confirmed=elevation_confirmed,
        acknowledge_incomplete=acknowledge_incomplete,
    )

    try:
        require_dossier_category(current, store, now)
    except (DossierCategoryMismatch, CategorizationNotCurrent) as exc:
        raise TransitionRejected([str(exc)]) from None

    readiness = None
    if to_state is DossierState.DECLARED:
        readiness = _declaration_readiness(
            store,
            snapshot,
            now,
            checks or StoredCheckResults(reference_now=now),
            declaration_evidence_id,
        )
    try:
        updated = transition(
            current,
            to_state,
            actor=resolved.actor,
            role=role,
            at=at,
            dossier_sha256=scan.snapshot_sha256 or "",
            conclusions=conclusions,
            corrective_measures=list(corrective_measures),
            manual_judgment_review=manual_judgment_review,
            readiness=readiness,
            elevated_to=resolved.elevated_to,
            elevated_at=at if to_state is DossierState.ANALYZED else None,
            elevation_confirmed=elevation_confirmed,
            analysis_sha256=resolved.analysis_sha256,
            declaration_evidence_id=declaration_evidence_id,
            acknowledged_incomplete=resolved.acknowledged_incomplete,
        )
    except ValidationError as exc:
        raise TransitionRejected(describe_validation("transición", exc)) from None
    except ValueError as exc:
        raise TransitionRejected([str(exc)]) from None

    entry = _write(store, scan, updated.transitions[-1], now)
    return Recorded(entry, updated, snapshot.notice)


def _write(
    store: WorkspaceStore, scan: TransitionScan, record: Transition, now: datetime
) -> TransitionEntry:
    seq = len(scan.entries) + 1
    stored = TransitionRecord(
        dossier_id=scan.dossier_id,
        seq=seq,
        previous_sha256=scan.last_file_sha256,
        recorded_at=now,
        transition=record,
    )
    data = chain.render_record(stored.model_dump(mode="json"))
    try:
        chain.reserve_sequence(store, _prefix(scan.dossier_id), seq)
    except chain.SequenceTaken:
        raise TransitionRejected(
            [f"{scan.dossier_id}: otra orden reservó antes la secuencia {seq}; revisa y reintenta"]
        ) from None
    key = record_key(stored, data)
    store.create_bytes(key, data)
    return TransitionEntry(key=key, sha256=record_sha256(record), recorded_at=now, record=record)


def _check_time(
    snapshot: DossierSnapshot, previous: TransitionEntry | None, at: datetime, now: datetime
) -> None:
    errors = []
    if at > now:
        errors.append("--at está en el futuro respecto al instante de registro")
    if at < snapshot.inputs.generated_at:
        errors.append("--at es anterior a la generación del expediente")
    if previous is not None and at < previous.record.at:
        errors.append("--at es anterior a la transición previa")
    if previous is not None and now < previous.recorded_at:
        errors.append(
            "el reloj va por detrás del último registro: escribirlo rompería la cadena; "
            "corrige la hora del sistema"
        )
    if errors:
        raise TransitionRejected(errors)


@dataclass(frozen=True)
class _Resolved:
    actor: str
    elevated_to: str | None = None
    analysis_sha256: str | None = None
    acknowledged_incomplete: list[int] = field(default_factory=list)


def _preconditions(
    store: WorkspaceStore,
    scan: TransitionScan,
    to_state: DossierState,
    *,
    by: str,
    role: Role,
    conclusions: str | None,
    corrective_measures: Sequence[CorrectiveMeasure],
    manual_judgment_review: bool,
    elevated_to: str | None,
    elevation_confirmed: bool,
    acknowledge_incomplete: bool,
) -> _Resolved:
    """Todo lo que se decide sin reconstruir ni evaluar el expediente."""
    snapshot, current = scan.snapshot, scan.dossier
    assert snapshot is not None and current is not None  # el llamador ya comprobó la integridad
    errors: list[str] = []
    if to_state is DossierState.DECLARED and snapshot.category is not Category.BASICA:
        raise TransitionRejected([CERTIFICATION_NOTE])
    _require_next(current.state, to_state)
    errors += check_declared_name(by)
    if (expected := _EXPECTED_ROLE.get(to_state)) is not None and role is not expected:
        errors.append(f"{to_state.value} lo registra el rol {expected.value}, no {role.value}")
    if to_state is DossierState.SELF_ASSESSED and role not in SELF_ASSESSORS:
        errors.append("SELF_ASSESSED lo registra el administrador del sistema o su delegado")
    if errors:
        raise TransitionRejected(errors)

    match to_state:
        case DossierState.SELF_ASSESSED:
            if missing := _missing_reviews(snapshot):
                raise TransitionRejected(missing)
            return _Resolved(by)
        case DossierState.ANALYZED:
            return _analysis(
                store,
                snapshot,
                by,
                conclusions,
                corrective_measures,
                manual_judgment_review,
                elevated_to,
                elevation_confirmed,
            )
        case DossierState.DECLARED:
            return _declaration(store, scan, by, acknowledge_incomplete)
    raise TransitionRejected([f"no se puede registrar {to_state.value}"])


def _require_next(state: DossierState, to_state: DossierState) -> None:
    index = ORDER.index(state)
    if state is DossierState.DECLARED:
        raise TransitionRejected(["el expediente ya está declarado; no admite más transiciones"])
    if to_state is DossierState.GENERATED or ORDER.index(to_state) <= index:
        raise TransitionRejected([f"el expediente ya está en {state.value}"])
    missing = ORDER[index + 1 : ORDER.index(to_state)]
    if missing:
        steps = ", ".join(f"{s.value} ({_STEP[s]})" for s in missing)
        raise TransitionRejected(
            [f"{to_state.value} exige antes {steps}; el expediente está en {state.value}"]
        )


def _missing_reviews(snapshot: DossierSnapshot) -> list[str]:
    try:
        rows = snapshot_export(snapshot).rows
    except (ValueError, KeyError):
        return ["la instantánea no tiene el resultado por medida en un formato legible"]
    if not rows:
        return ["la instantánea no tiene filas de resultado por medida que revisar"]
    unreviewed = [row.measure_id for row in rows if row.last_review is None]
    if not unreviewed:
        return []
    shown = ", ".join(unreviewed[:_SHOWN_ROWS])
    more = f" y {len(unreviewed) - _SHOWN_ROWS} más" if len(unreviewed) > _SHOWN_ROWS else ""
    return [
        f"SELF_ASSESSED exige una revisión en cada fila; sin revisión: {shown}{more}",
    ]


# --- personas declaradas -----------------------------------------------------------------------


def _declared(store: WorkspaceStore, role: Role, name: str, flag: str) -> tuple[str, list[str]]:
    """El nombre tal como lo declaran las respuestas de org-docs para ese rol."""
    try:
        return declared_signatory(store, role, name), []
    except DocumentApprovalRejected as exc:
        return name, [f"{flag}: {message}" for message in exc.errors]


def _analysis(
    store: WorkspaceStore,
    snapshot: DossierSnapshot,
    by: str,
    conclusions: str | None,
    corrective_measures: Sequence[CorrectiveMeasure],
    manual_judgment_review: bool,
    elevated_to: str | None,
    elevation_confirmed: bool,
) -> _Resolved:
    errors: list[str] = []
    actor, problems = _declared(store, Role.SECURITY_OFFICER, by, "--by")
    errors += problems
    if not document_status(store, DESIGNATION_DOCUMENT).current:
        errors.append(f"{DESIGNATION_DOCUMENT} no está aprobada y vigente")
    if not conclusions or not conclusions.strip():
        errors.append("conclusiones: el análisis las exige")
    else:
        errors += free_text_problems("conclusiones", conclusions)
    for index, measure in enumerate(corrective_measures):
        errors += free_text_problems(f"medidas correctoras[{index}]", measure.description)
    target = ""
    if not elevated_to or not elevated_to.strip():
        errors.append("--elevated-to: indica la persona del responsable del sistema")
    else:
        target, problems = _declared(store, Role.SYSTEM_OWNER, elevated_to, "--elevated-to")
        errors += problems
    if not elevation_confirmed:
        errors.append(
            "--elevated: confirma que las conclusiones se elevaron al responsable del sistema"
        )
    if snapshot.category is Category.BASICA and not manual_judgment_review:
        errors.append(MANUAL_REVIEW_REQUIRED)
    if errors:
        raise TransitionRejected(sorted(set(errors)))
    return _Resolved(actor, elevated_to=target)


def _blocker_labels(evaluation: AssessmentResult) -> str:
    labels = dict.fromkeys(
        f"{KIND_FALLBACK[b.kind]} ({b.measure_id})" if b.measure_id else KIND_FALLBACK[b.kind]
        for b in evaluation.readiness.blockers
    )
    shown = list(labels)[:_SHOWN_ROWS]
    more = f" y {len(labels) - _SHOWN_ROWS} más" if len(labels) > _SHOWN_ROWS else ""
    return "; ".join(shown) + more


def evaluate_for_declaration(
    store: WorkspaceStore, now: datetime, checks: CheckResultsSource
) -> tuple[AssessmentResult, list[str]]:
    """La evaluación actual y por qué no permite declarar (vacío si está READY y se leyó entera)."""
    evaluation = assess(store, now, checks=checks)
    problems: list[str] = []
    if evaluation.errors:
        problems.append(
            f"la evaluación actual tiene {len(evaluation.errors)} error(es) de lectura; "
            "revisa `ens-evidence matrix show`"
        )
    if evaluation.readiness.readiness is not Readiness.READY:
        problems.append(
            f"la evaluación actual no está preparada: {_blocker_labels(evaluation)}. "
            "Detalle: `ens-evidence assessment status`"
        )
    return evaluation, problems


def _declaration(
    store: WorkspaceStore, scan: TransitionScan, by: str, acknowledge_incomplete: bool
) -> _Resolved:
    """Guardas puras de DECLARED: persona, análisis citado y secciones incompletas."""
    snapshot = scan.snapshot
    assert snapshot is not None
    errors: list[str] = []
    actor, problems = _declared(store, Role.MANAGEMENT, by, "--by")
    errors += problems
    if snapshot_readiness(snapshot) is not Readiness.READY:
        errors.append("el sistema no estaba preparado al generar este expediente; genera uno nuevo")
    analysis = scan.entries[-1] if scan.entries else None
    if analysis is None or analysis.record.to_state is not DossierState.ANALYZED:
        errors.append("falta el registro ANALYZED que la declaración debe citar")
    incomplete = _incomplete(snapshot)
    if incomplete and not acknowledge_incomplete:
        listed = ", ".join(f"{n} «{snapshot.section(n).title}»" for n in incomplete)
        errors.append(
            f"el expediente tiene secciones incompletas ({listed}); complétalas y genera un "
            "expediente nuevo, o reconócelas con --acknowledge-incomplete"
        )
    if errors:
        raise TransitionRejected(sorted(set(errors)))
    assert analysis is not None
    return _Resolved(
        actor,
        analysis_sha256=analysis.sha256,
        acknowledged_incomplete=incomplete if acknowledge_incomplete else [],
    )


def _declaration_readiness(
    store: WorkspaceStore,
    snapshot: DossierSnapshot,
    now: datetime,
    checks: CheckResultsSource,
    evidence_id: str | None,
) -> ReadinessResult:
    """La preparación que cita DECLARED: la actual debe ser READY y la instantánea, coherente
    consigo misma. No se repite la evaluación congelada: una recolección posterior de evidencias
    la haría fallar aunque nada derive, y la deriva ya compara filas y fuentes."""
    evaluation, errors = evaluate_for_declaration(store, now, checks)
    export = snapshot_export(snapshot)
    if export.readiness is Readiness.READY and (export.blockers or export.errors):
        errors.append(
            "el expediente dice que el sistema estaba preparado pero registra bloqueos o errores; "
            "genera un expediente nuevo"
        )
    if evidence_id is not None:
        errors += [f"--evidence: {problem}" for problem in verify_one(store, evidence_id).problems]
    if errors:
        raise TransitionRejected(sorted(set(errors)))
    return evaluation.readiness


__all__ = [
    "CERTIFICATION_NOTE",
    "Recorded",
    "TransitionRejected",
    "TransitionScan",
    "entries",
    "evaluate_for_declaration",
    "record_key",
    "record_transition",
    "scan_transitions",
    "seq_key",
]
