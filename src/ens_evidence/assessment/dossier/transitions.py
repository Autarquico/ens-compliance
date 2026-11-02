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

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

import yaml
from pydantic import AwareDatetime, Field, ValidationError, model_validator

from ens_evidence.assessment.dossier.drift import rebuild_and_diff
from ens_evidence.assessment.dossier.model import DossierSnapshot
from ens_evidence.assessment.dossier.render import TransitionEntry
from ens_evidence.assessment.dossier.store import (
    ASSESSMENTS_PREFIX,
    DossierUnreadable,
    load_snapshot,
    stored_snapshot_sha256,
)
from ens_evidence.categorization import CategorizationNotCurrent
from ens_evidence.categorization.dossier import DossierCategoryMismatch, require_dossier_category
from ens_evidence.checks.source import StoredCheckResults
from ens_evidence.evidence.verify import verify_one
from ens_evidence.incidents.privacy import check_declared_name
from ens_evidence.matrix import CheckResultsSource, assess
from ens_evidence.matrix.evaluate import AssessmentResult
from ens_evidence.matrix.store import instant_stamp
from ens_evidence.models.contracts import SHA256_PATTERN, Strict
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
from ens_evidence.orgdocs.answers import read_answers, validate_answers
from ens_evidence.orgdocs.approval import (
    DocumentApprovalRejected,
    _declared_signatory,
    document_status,
)
from ens_evidence.orgdocs.template import load_templates
from ens_evidence.privacy_text import has_credential, text_problems
from ens_evidence.scope.messages import describe_validation
from ens_evidence.scope.secrets import find_secrets
from ens_evidence.workspace import AlreadyExists, NotFound, WorkspaceStore
from ens_evidence.yaml_unique import safe_load_unique

DESIGNATION_DOCUMENT = "designacion-responsables"
TRANSITIONS_DIR = "transitions"
RECORD_NAME = re.compile(r"(\d{6})-(\d{8}T\d{12}Z)-([0-9a-f]{12})\.yaml")
RESERVATION_NAME = re.compile(r"seq/(\d{6})")
_RESERVED = b"reservada\n"
_RESULTS_SECTION = 6  # índice de la sección 7, «Resultado por medida»
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
CERTIFICATION_NOTE = (
    "en categoría MEDIA/ALTA la conformidad exige auditoría de certificación (art. 38.1): "
    "la herramienta no registra una declaración"
)


class TransitionRejected(Exception):
    """No se escribe nada; `errors` dice por qué (rutas y tipos, nunca valores)."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


class TransitionRecord(Strict):
    dossier_id: str
    seq: int = Field(ge=1)
    previous_sha256: str | None = Field(pattern=SHA256_PATTERN)
    recorded_at: AwareDatetime
    transition: Transition

    @model_validator(mode="after")
    def _first_has_no_predecessor(self) -> "TransitionRecord":
        if (self.seq == 1) != (self.previous_sha256 is None):
            raise ValueError("only the first record has no previous_sha256")
        return self


@dataclass(frozen=True)
class _Stored:
    key: str
    file_sha256: str
    record: TransitionRecord


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
    return f"{_prefix(dossier)}/seq/{seq:06d}"


def record_key(record: TransitionRecord, data: bytes) -> str:
    sha12 = hashlib.sha256(data).hexdigest()[:12]
    stamp = instant_stamp(record.recorded_at)
    return f"{_prefix(record.dossier_id)}/{record.seq:06d}-{stamp}-{sha12}.yaml"


def _render(record: TransitionRecord) -> bytes:
    payload = record.model_dump(mode="json")
    return yaml.safe_dump(payload, sort_keys=True, allow_unicode=True).encode("utf-8")


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
    problems: list[str] = []
    stored, reserved = _read_keys(store, dossier, problems)
    by_seq: dict[int, list[_Stored]] = {}
    for item in stored:
        by_seq.setdefault(item.record.seq, []).append(item)
    problems += _sequence_problems(by_seq, reserved)
    problems += _record_problems(stored, by_seq, snapshot, snapshot_sha)
    entries = tuple(_entry(item) for item in stored)
    dossier_state = None if problems else _reconstruct(snapshot, stored, problems)
    return TransitionScan(
        dossier,
        snapshot,
        snapshot_sha,
        entries,
        tuple(problems),
        dossier_state,
        stored[-1].file_sha256 if stored else None,
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


def _read_keys(
    store: WorkspaceStore, dossier: str, problems: list[str]
) -> tuple[list[_Stored], set[int]]:
    prefix = _prefix(dossier)
    stored: list[_Stored] = []
    reserved: set[int] = set()
    for key in store.list(prefix):
        name = key.removeprefix(f"{prefix}/")
        if (match := RESERVATION_NAME.fullmatch(name)) is not None:
            reserved.add(int(match.group(1)))
        elif RECORD_NAME.fullmatch(name) is not None:
            if (item := _read_record(store, dossier, key, problems)) is not None:
                stored.append(item)
        else:
            problems.append(f"{key}: clave inesperada en las transiciones")
    stored.sort(key=lambda s: (s.record.seq, s.key))
    return stored, reserved


def _read_record(
    store: WorkspaceStore, dossier: str, key: str, problems: list[str]
) -> _Stored | None:
    raw = None
    try:
        data = store.read_bytes(key)
        raw = safe_load_unique(data.decode("utf-8"))
        record = TransitionRecord.model_validate(raw)
    except NotFound:
        problems.append(f"{key}: desapareció mientras se leía")
    except UnicodeDecodeError:
        problems.append(f"{key}: no es texto UTF-8")
    except yaml.YAMLError:
        problems.append(f"{key}: YAML no válido o con una clave repetida")
    except ValidationError as exc:
        problems += describe_validation(key, exc, raw)
    else:
        if record.dossier_id != dossier:
            problems.append(f"{key}: el registro es de otro expediente")
            return None
        if key != record_key(record, data):
            problems.append(
                f"{key}: la clave no coincide con el contenido (número, instante o hash)"
            )
        return _Stored(key, hashlib.sha256(data).hexdigest(), record)
    return None


def _sequence_problems(by_seq: dict[int, list[_Stored]], reserved: set[int]) -> list[str]:
    problems = [
        f"bifurcación: {len(group)} registros con el mismo número {seq}: "
        + ", ".join(s.key for s in group)
        for seq, group in sorted(by_seq.items())
        if len(group) > 1
    ]
    last = max(by_seq, default=0)
    problems += [f"falta el registro {seq}" for seq in range(1, last + 1) if seq not in by_seq]
    problems += [
        f"reserva de la secuencia {seq} sin registro" for seq in sorted(reserved - set(by_seq))
    ]
    problems += [
        f"registro {seq} sin reserva de la secuencia" for seq in sorted(set(by_seq) - reserved)
    ]
    return sorted(set(problems))


def _record_problems(
    stored: list[_Stored],
    by_seq: dict[int, list[_Stored]],
    snapshot: DossierSnapshot,
    snapshot_sha: str,
) -> list[str]:
    problems: list[str] = []
    linear = [s for s in stored if len(by_seq[s.record.seq]) == 1]
    for before, after in zip(linear, linear[1:], strict=False):
        if after.record.seq == before.record.seq + 1 and (
            after.record.previous_sha256 != before.file_sha256
        ):
            problems.append(
                f"registros {before.record.seq} y {after.record.seq}: "
                "la cadena previous_sha256 está rota"
            )
    for item in stored:
        record = item.record
        if record.transition.dossier_sha256 != snapshot_sha:
            problems.append(f"{item.key}: lleva la huella de otro dossier.json")
        if record.transition.at < snapshot.inputs.generated_at:
            problems.append(f"{item.key}: at es anterior a la generación del expediente")
        if record.transition.at > record.recorded_at:
            problems.append(f"{item.key}: at es posterior a recorded_at")
    problems += [
        f"registros {a.record.seq} y {b.record.seq}: recorded_at retrocede"
        for a, b in zip(linear, linear[1:], strict=False)
        if b.record.recorded_at < a.record.recorded_at
    ]
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
    if declared is not None and _snapshot_readiness(snapshot) is not Readiness.READY:
        problems.append("DECLARED sobre una instantánea que no estaba READY")
        return None
    return dossier


def _snapshot_readiness(snapshot: DossierSnapshot) -> Readiness | None:
    content = snapshot.sections[_RESULTS_SECTION].content
    matrix = content.get("matrix") if isinstance(content, dict) else None
    value = matrix.get("readiness") if isinstance(matrix, dict) else None
    try:
        return Readiness(value)
    except ValueError:
        return None


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
) -> Recorded:
    """Añade una transición. `TransitionRejected` si algo falla: no se escribe nada.

    `at` es cuándo la persona hizo el acto; `now`, cuándo se registra. La preparación (READY) se
    evalúa aquí, nunca la aporta el llamador."""
    scan = scan_transitions(store, dossier)
    if scan.problems or scan.snapshot is None or scan.dossier is None:
        raise TransitionRejected(
            [f"{dossier} está roto y no admite más transiciones", *scan.problems]
        )
    snapshot, current = scan.snapshot, scan.dossier
    previous = current.transitions[-1] if current.transitions else None

    _check_time(snapshot, previous, at, now)
    source = checks or StoredCheckResults(reference_now=now)
    drift = rebuild_and_diff(store, snapshot, checks=source).drift
    if drift:
        raise TransitionRejected(
            [
                "el expediente ya no describe el estado actual; genera uno nuevo",
                *(f"{section}: {line}" for section, lines in drift.items() for line in lines),
            ]
        )
    try:
        require_dossier_category(current, store, now)
    except (DossierCategoryMismatch, CategorizationNotCurrent) as exc:
        raise TransitionRejected([str(exc)]) from None

    extra = _preconditions(
        store,
        scan,
        to_state,
        by=by,
        role=role,
        now=now,
        checks=source,
        conclusions=conclusions,
        corrective_measures=corrective_measures,
        elevated_to=elevated_to,
        elevation_confirmed=elevation_confirmed,
        declaration_evidence_id=declaration_evidence_id,
    )
    try:
        updated = transition(
            current,
            to_state,
            actor=extra.actor,
            role=role,
            at=at,
            dossier_sha256=scan.snapshot_sha256 or "",
            conclusions=conclusions,
            corrective_measures=list(corrective_measures),
            manual_judgment_review=manual_judgment_review,
            readiness=extra.readiness,
            elevated_to=extra.elevated_to,
            elevated_at=at if to_state is DossierState.ANALYZED else None,
            elevation_confirmed=elevation_confirmed,
            analysis_sha256=extra.analysis_sha256,
            declaration_evidence_id=declaration_evidence_id,
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
    data = _render(stored)
    try:
        store.create_bytes(seq_key(scan.dossier_id, seq), _RESERVED)
    except AlreadyExists:
        raise TransitionRejected(
            [f"{scan.dossier_id}: otra orden reservó antes la secuencia {seq}; revisa y reintenta"]
        ) from None
    key = record_key(stored, data)
    store.create_bytes(key, data)
    return TransitionEntry(key=key, sha256=record_sha256(record), recorded_at=now, record=record)


def _check_time(
    snapshot: DossierSnapshot, previous: Transition | None, at: datetime, now: datetime
) -> None:
    errors = []
    if at > now:
        errors.append("--at está en el futuro respecto al instante de registro")
    if at < snapshot.inputs.generated_at:
        errors.append("--at es anterior a la generación del expediente")
    if previous is not None and at < previous.at:
        errors.append("--at es anterior a la transición previa")
    if errors:
        raise TransitionRejected(errors)


@dataclass(frozen=True)
class _Resolved:
    actor: str
    readiness: ReadinessResult | None = None
    elevated_to: str | None = None
    analysis_sha256: str | None = None


def _preconditions(
    store: WorkspaceStore,
    scan: TransitionScan,
    to_state: DossierState,
    *,
    by: str,
    role: Role,
    now: datetime,
    checks: CheckResultsSource,
    conclusions: str | None,
    corrective_measures: Sequence[CorrectiveMeasure],
    elevated_to: str | None,
    elevation_confirmed: bool,
    declaration_evidence_id: str | None,
) -> _Resolved:
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
                store, by, conclusions, corrective_measures, elevated_to, elevation_confirmed
            )
        case DossierState.DECLARED:
            return _declaration(store, scan, by, now, checks, declaration_evidence_id)
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
    content = snapshot.sections[_RESULTS_SECTION].content
    matrix = content.get("matrix") if isinstance(content, dict) else None
    rows = matrix.get("rows") if isinstance(matrix, dict) else None
    if not isinstance(rows, list) or not rows:
        return ["la instantánea no tiene filas de resultado por medida que revisar"]
    unreviewed = [
        str(row.get("measure_id"))
        for row in rows
        if isinstance(row, dict) and not row.get("last_review")
    ]
    if not unreviewed:
        return []
    shown = ", ".join(unreviewed[:_SHOWN_ROWS])
    more = f" y {len(unreviewed) - _SHOWN_ROWS} más" if len(unreviewed) > _SHOWN_ROWS else ""
    return [
        f"SELF_ASSESSED exige una revisión en cada fila; sin revisión: {shown}{more}",
    ]


# --- personas declaradas -----------------------------------------------------------------------


def _answers(store: WorkspaceStore) -> dict:
    try:
        return dict(validate_answers(read_answers(store), load_templates()).values)
    except ValueError:
        return {}


def _declared(store: WorkspaceStore, role: Role, name: str, flag: str) -> tuple[str, list[str]]:
    """El nombre tal como lo declaran las respuestas de org-docs para ese rol."""
    try:
        return _declared_signatory(role, name, _answers(store)), []
    except DocumentApprovalRejected as exc:
        return name, [f"{flag}: {message}" for message in exc.errors]


def _text_errors(label: str, text: str) -> list[str]:
    found = [f"{label}: {problem}" for problem in text_problems(text)[0]]
    found += [f"{label}: parece un secreto" for _ in find_secrets(text, label)]
    if has_credential(text):
        found.append(f"{label}: contiene una credencial")
    return sorted(set(found))


def _analysis(
    store: WorkspaceStore,
    by: str,
    conclusions: str | None,
    corrective_measures: Sequence[CorrectiveMeasure],
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
        errors += _text_errors("conclusiones", conclusions)
    for index, measure in enumerate(corrective_measures):
        errors += _text_errors(f"medidas correctoras[{index}]", measure.description)
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
    if errors:
        raise TransitionRejected(sorted(set(errors)))
    return _Resolved(actor, elevated_to=target)


def evaluate_for_declaration(
    store: WorkspaceStore, now: datetime, checks: CheckResultsSource
) -> tuple[AssessmentResult, list[str]]:
    """La evaluación actual y por qué no permite declarar (vacío si está READY y se leyó entera)."""
    evaluation = assess(store, now, checks=checks)
    problems: list[str] = []
    if evaluation.errors:
        problems.append(
            f"la evaluación actual tiene {len(evaluation.errors)} error(es) de lectura; "
            "revisa `matrix show`"
        )
    if evaluation.readiness.readiness is not Readiness.READY:
        blockers = ", ".join(
            f"{b.kind.value}:{b.measure_id or '-'}"
            for b in evaluation.readiness.blockers[:_SHOWN_ROWS]
        )
        problems.append(f"la evaluación actual no está READY ({blockers})")
    return evaluation, problems


def _declaration(
    store: WorkspaceStore,
    scan: TransitionScan,
    by: str,
    now: datetime,
    checks: CheckResultsSource,
    evidence_id: str | None,
) -> _Resolved:
    snapshot, current = scan.snapshot, scan.dossier
    assert snapshot is not None and current is not None
    errors: list[str] = []
    actor, problems = _declared(store, Role.MANAGEMENT, by, "--by")
    errors += problems
    if _snapshot_readiness(snapshot) is not Readiness.READY:
        errors.append("la instantánea no estaba READY; genera un expediente nuevo")
    evaluation, problems = evaluate_for_declaration(store, now, checks)
    errors += problems
    if evidence_id is not None:
        errors += [f"--evidence: {problem}" for problem in verify_one(store, evidence_id).problems]
    analysis = scan.entries[-1] if scan.entries else None
    if analysis is None or analysis.record.to_state is not DossierState.ANALYZED:
        errors.append("falta el registro ANALYZED que la declaración debe citar")
    if errors:
        raise TransitionRejected(sorted(set(errors)))
    assert analysis is not None
    return _Resolved(actor, readiness=evaluation.readiness, analysis_sha256=analysis.sha256)


__all__ = [
    "CERTIFICATION_NOTE",
    "Recorded",
    "TransitionRecord",
    "TransitionRejected",
    "TransitionScan",
    "entries",
    "evaluate_for_declaration",
    "record_key",
    "record_transition",
    "scan_transitions",
    "seq_key",
]
