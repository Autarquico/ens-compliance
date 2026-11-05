"""Construye el workspace de un perfil del banco con las APIs del producto y un reloj fijo.

Cada paso hace lo que haría una persona: editar los YAML del alcance, aprobar con la función del
producto, decidir la aplicabilidad e importar evidencias. Nada se escribe a mano en `evidence/`
ni en `assessment/`. Los instantes salen de `clock.start` (`now=` explícito) y los ids de
evidencia de una fuente sembrada: el mismo perfil y la misma semilla dan los mismos bytes.

Los pasos están en `STEPS`, en orden, y terminan en la instantánea del expediente, como la
genera `assessment dossier generate`. `inspect` le pasa después la capa determinista de la
pre-auditoría y calcula la huella del payload, sin enviar nada.
"""

import hashlib
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path

from pydantic import ValidationError

from ens_evidence.applicability import approve_declaration, propose_applicability
from ens_evidence.applicability.context import Context, load_context
from ens_evidence.approvals import Rejected
from ens_evidence.assessment.dossier import build_snapshot, current_inputs, render_snapshot_md
from ens_evidence.assessment.dossier.build import DossierRejected
from ens_evidence.assessment.dossier.store import load_snapshot, save_markdown, save_snapshot
from ens_evidence.benchmark.cases import (
    BenchError,
    load_profile,
    profile_files_dir,
    read_bench_bytes,
)
from ens_evidence.benchmark.model import (
    ApplicabilityDecision,
    EvidenceSource,
    MatrixRow,
    Person,
    Profile,
    ProfileEvidence,
)
from ens_evidence.catalog.controls import init_measures, load_measures, save_measures
from ens_evidence.categorization import (
    CategorizationNotCurrent,
    approve_categorization,
    categorization_status,
)
from ens_evidence.checks.engine import OutcomeKind, run_all
from ens_evidence.checks.source import StoredCheckResults
from ens_evidence.evidence.importer import (
    EvidenceImportError,
    import_collected,
    import_manual,
)
from ens_evidence.incidents import IncidentRejected
from ens_evidence.incidents.model import (
    ActionKind,
    IncidentState,
    IncidentStatus,
    RecordKind,
    ReviewResult,
    TriState,
)
from ens_evidence.incidents.store import (
    Recorded,
    next_incident_id,
    record_incident,
    simulation_procedure,
)
from ens_evidence.matrix import (
    CheckLink,
    DocumentsSnapshot,
    ManualEvidenceLink,
    MatrixRejected,
    OrgdocsSource,
    RowKind,
    add_link,
    init_matrix,
    plan_obligations,
    prepare_review,
    record_review,
    set_entry,
)
from ens_evidence.matrix import DocumentLink as MatrixDocumentLink
from ens_evidence.models import MeasureAssessment
from ens_evidence.models.contracts import CollectionResult, EvidenceManifest
from ens_evidence.models.dossier import SELF_ASSESSORS
from ens_evidence.models.enums import Applicability, Role
from ens_evidence.models.preaudit import Finding
from ens_evidence.models.readiness import Measure
from ens_evidence.orgdocs import (
    AnswersInvalid,
    DocumentApprovalRejected,
    approve_document,
    build_documents,
    document_names,
    load_templates,
    read_answers,
    validate_answers,
)
from ens_evidence.orgdocs.approval import required_role
from ens_evidence.preaudit.deterministic import deterministic_findings
from ens_evidence.preaudit.pack import load_pack
from ens_evidence.preaudit.payload import build_payload
from ens_evidence.preaudit.service import dossier_problems
from ens_evidence.scope import (
    ScopeApprovalRejected,
    ScopeNotApproved,
    approve_scope,
    load_inventory,
)
from ens_evidence.scope.messages import describe_validation
from ens_evidence.workspace import LocalStore, WorkspaceStore

SCOPE_DIR = "scope"
ANSWERS_FILE = "org/answers.yaml"
CONNECTORS_DIR = "connectors"
COLLECTOR_VERSION = "benchmark"
"""Versión declarada de las recogidas del banco: el JSON es un fixture, no salió de un collector."""
TICK = timedelta(minutes=1)


@dataclass
class SeededIds:
    """Ids de evidencia con el formato del producto, derivados de `seed` y de un contador."""

    seed: str
    issued: int = 0

    def __call__(self, now: datetime) -> str:
        digest = hashlib.sha256(f"{self.seed}:{self.issued}".encode()).hexdigest()
        self.issued += 1
        return f"EV-{now:%Y%m%d}-{digest[:8]}"


def seeded_ids(seed: str) -> SeededIds:
    return SeededIds(seed)


@dataclass
class FrozenDocuments:
    """El estado de org-docs leído una vez: la matriz no lo cambia, y leerlo en cada enlace y
    en cada revisión cuesta casi todo el tiempo de la construcción."""

    frozen: DocumentsSnapshot

    def snapshot(self, store: WorkspaceStore, at: datetime) -> DocumentsSnapshot:
        return self.frozen


@dataclass
class Build:
    """Estado de una construcción: el workspace, su reloj y los ids reales de cada alias."""

    profile: Profile
    files: Path
    root: Path
    id_factory: SeededIds
    evidence_ids: dict[str, str] = field(default_factory=dict)
    dossier: str | None = None
    documents: FrozenDocuments | None = None
    ticks: int = 0
    judgment: bool = True
    """False en un caso solo determinista: su payload no se construye (`inspect`)."""

    @property
    def store(self) -> LocalStore:
        return LocalStore(self.root)

    def tick(self) -> datetime:
        """El siguiente instante del reloj fijo, un minuto después del anterior."""
        moment = self.profile.clock.start + self.ticks * TICK
        self.ticks += 1
        return moment

    @property
    def now(self) -> datetime:
        """El último instante usado (o `clock.start` si aún no se usó ninguno)."""
        return self.profile.clock.start + max(self.ticks - 1, 0) * TICK

    def signer(self, role: Role) -> str:
        """Nombre de la primera persona del perfil con `role`."""
        for person in self.profile.people:
            if role in person.roles:
                return person.name
        raise BenchError([f"nadie tiene el rol {role.value}"])

    def person(self, person_id: str) -> Person:
        """La persona declarada con ese id (el modelo ya garantiza que existe)."""
        return next(p for p in self.profile.people if p.id == person_id)

    def fork(self, destination: Path) -> "Build":
        """Una copia del workspace en `destination` que sigue con el mismo reloj y los mismos
        ids: lo que se haga en ella no toca el original."""
        shutil.copytree(self.root, destination)
        return replace(
            self,
            root=destination,
            id_factory=replace(self.id_factory),
            evidence_ids=dict(self.evidence_ids),
        )


Step = Callable[[Build], None]


def _scope(build: Build) -> None:
    """Los YAML del alcance y su categorización, como los edita una persona; y su aprobación."""
    source = build.files / SCOPE_DIR
    if not source.is_dir():
        raise BenchError([f"falta el directorio files/{SCOPE_DIR}/"])
    _copy_tree(build, SCOPE_DIR)
    approve_scope(build.store, build.signer(Role.SYSTEM_OWNER), Role.SYSTEM_OWNER, build.tick())


def _categorization(build: Build) -> None:
    now = build.tick()
    approve_categorization(build.store, build.signer(Role.SECURITY_OFFICER), now)
    computation = categorization_status(build.store, now).computation
    assert computation is not None
    computed = {item.dimension: item.level for item in computation.levels if item.level}
    if computed != build.profile.categorization:
        raise BenchError(
            [
                f"los niveles calculados ({_levels(computed)}) no son los del perfil "
                f"({_levels(build.profile.categorization)})"
            ]
        )


def _levels(levels: dict) -> str:
    return ", ".join(f"{d.value}={levels[d].value}" for d in sorted(levels)) or "ninguno"


def _applicability(build: Build) -> None:
    """Propuesta del producto, decisiones del perfil por encima y firma de la Declaración."""
    init_measures(build.store)
    now = build.tick()
    propose_applicability(build.store, now)
    context = load_context(build.store, now)
    document = load_measures(build.store)
    assert document is not None
    decisions = {d.measure: d for d in build.profile.applicability}
    measures = [_decided(m, decisions.get(m.id), context, build.profile) for m in document.measures]
    if undecided := [m.id for m in measures if m.applicability is Applicability.UNDETERMINED]:
        raise BenchError(
            [
                "medidas sin decidir, ni en `applicability` ni con "
                f"`not_required_justification`: {', '.join(undecided)}"
            ]
        )
    save_measures(build.store, document.model_copy(update={"measures": measures}))
    approve_declaration(build.store, build.signer(Role.SECURITY_OFFICER), build.tick())


def _decided(
    measure: Measure, decision: ApplicabilityDecision | None, context: Context, profile: Profile
) -> Measure:
    """La medida tras la decisión humana del perfil; sin decisión, la propuesta queda intacta."""
    update: dict = {}
    if decision is not None:
        update = {
            "applicability": (
                Applicability.APPLICABLE if decision.applicable else Applicability.NOT_APPLICABLE
            ),
            "justification": decision.justification,
        }
    elif measure.applicability is Applicability.UNDETERMINED and profile.not_required_justification:
        update = {
            "applicability": Applicability.NOT_APPLICABLE,
            "justification": profile.not_required_justification,
        }
    if update:
        update |= {"proposed": False, "selection": context.basis(measure.id)}
    if measure.id in profile.reinforcements:
        update["chosen_reinforcements"] = profile.reinforcements[measure.id]
    return measure.model_copy(update=update) if update else measure


def _evidence(build: Build) -> None:
    for evidence in build.profile.evidence:
        path = build.files / evidence.file
        if not path.is_file():
            raise BenchError([f"{evidence.alias}: falta files/{evidence.file}"])
        importer = _manual if evidence.source is EvidenceSource.MANUAL else _collected
        build.evidence_ids[evidence.alias] = importer(
            build, evidence, read_bench_bytes(path, build.files)
        ).id


def _manual(build: Build, evidence: ProfileEvidence, data: bytes) -> EvidenceManifest:
    inventory = load_inventory(build.store).inventory
    assert inventory is not None  # el alcance ya está aprobado
    if evidence.asset is None or evidence.asset not in inventory.system.included_assets:
        shown = evidence.asset or "sin `asset`"
        raise BenchError(
            [f"{evidence.alias}: una evidencia manual nombra un activo del alcance ({shown})"]
        )
    return import_manual(
        build.store,
        data,
        asset_id=evidence.asset,
        submitted_by=build.signer(Role.SECURITY_OFFICER),
        now=build.tick(),
        valid_until=evidence.valid_until,
        id_factory=build.id_factory,
    )


def _collected(build: Build, evidence: ProfileEvidence, data: bytes) -> EvidenceManifest:
    if evidence.valid_until is not None:
        raise BenchError(
            [f"{evidence.alias}: una recogida no lleva `valid_until`; caduca por su check"]
        )
    try:
        result = CollectionResult.model_validate_json(data)
    except ValidationError as exc:
        raise BenchError(describe_validation(f"files/{evidence.file}", exc)) from None
    if evidence.asset is not None and evidence.asset != result.asset_id:
        raise BenchError([f"{evidence.alias}: `asset` ({evidence.asset}) no es el de la recogida"])
    return import_collected(
        build.store,
        result,
        collector_version=COLLECTOR_VERSION,
        now=build.tick(),
        id_factory=build.id_factory,
    )


def _copy_tree(build: Build, directory: str) -> None:
    """Los ficheros de `files/<directory>/` en `<directory>/`, como los escribe una persona."""
    source = build.files / directory
    for path in sorted(p for p in source.rglob("*") if p.is_file()):
        build.store.write_bytes(
            f"{directory}/{path.relative_to(source).as_posix()}",
            read_bench_bytes(path, build.files),
        )


def _orgdocs(build: Build) -> None:
    """Respuestas del perfil, `docs build` y la aprobación de cada documento por su firmante."""
    answers = build.files / ANSWERS_FILE
    if not answers.is_file():
        raise BenchError([f"falta files/{ANSWERS_FILE}"])
    build.store.write_bytes(ANSWERS_FILE, read_bench_bytes(answers, build.files))
    try:
        build_documents(build.store)
    except AnswersInvalid as exc:
        raise BenchError(exc.report.errors()) from None
    approve_documents(build)


def approve_documents(build: Build) -> None:
    """Cada documento generado, aprobado por el firmante que le da el perfil, con su rol."""
    approvers = {d.ref: d.approved_by for d in build.profile.documents}
    if missing := [name for name in document_names() if name not in approvers]:
        raise BenchError([f"documentos sin firmante en `documents`: {', '.join(missing)}"])
    if unknown := sorted(set(approvers) - set(document_names())):
        raise BenchError([f"documentos desconocidos en `documents`: {', '.join(unknown)}"])
    values = validate_answers(read_answers(build.store), load_templates()).values
    for name in document_names():
        role = required_role(name, values)
        person = build.person(approvers[name])
        if role is None or role not in person.roles:
            expected = role.value if role else "sin declarar"
            raise BenchError([f"{name}: lo aprueba el rol {expected}, que {person.id} no tiene"])
        approve_document(build.store, name, person.name, role, build.tick())


def _drill(build: Build) -> None:
    """El simulacro del perfil, como `incident new --simulated`, `update` y `close`."""
    drill = build.profile.incident_drill
    if drill is None:
        return
    by = build.person(drill.by).name
    detected = build.tick()
    opened = IncidentState(
        id=next_incident_id(build.store, simulated=True, year=detected.year),
        simulated=True,
        title=drill.title,
        detected_at=detected,
        procedure=simulation_procedure(build.store),
    )
    _record(build, opened, RecordKind.OPENED, by)
    acted = build.tick()
    updated = _changed(
        opened,
        personal_data=TriState.NO,
        actions=[{"kind": ActionKind.EMERGENCY, "at": acted, "description": drill.action}],
    )
    _record(build, updated, RecordKind.UPDATED, by, acted)
    closed_at = build.tick()
    closure = {
        "closed_at": closed_at,
        "conclusion": drill.conclusion,
        "legal_relevance": TriState.NO,
        "auditable_events_review": {
            "result": ReviewResult.NO_CHANGE,
            "detail": drill.auditable_events_detail,
        },
    }
    closed = _changed(updated, status=IncidentStatus.CLOSED, closure=closure)
    recorded = _record(build, closed, RecordKind.CLOSED, by, closed_at)
    if recorded.history_evidence_id is None:
        raise BenchError([f"{closed.id}: sin historial", *recorded.history_problems])
    build.evidence_ids[drill.alias] = recorded.history_evidence_id


def _changed(state: IncidentState, **changes: object) -> IncidentState:
    return IncidentState.model_validate({**state.model_dump(mode="json"), **changes})


def _record(
    build: Build, state: IncidentState, kind: RecordKind, by: str, now: datetime | None = None
) -> Recorded:
    return record_incident(
        build.store, state, kind=kind, by=by, now=now or build.tick(), id_factory=build.id_factory
    )


def _checks(build: Build) -> None:
    """La configuración de los conectores y `check --all` sobre lo ya recogido."""
    if not (build.files / CONNECTORS_DIR).is_dir():
        return
    _copy_tree(build, CONNECTORS_DIR)
    for outcome in run_all(build.store, build.tick()):
        if outcome.kind in (OutcomeKind.REJECTED, OutcomeKind.HISTORY_UNREADABLE):
            raise BenchError([f"{outcome.connector}: {outcome.kind.value}", *outcome.detail])


def _matrix(build: Build) -> None:
    """`matrix init`, responsable y periodicidad, enlaces y la revisión de cada medida."""
    init_matrix(build.store, build.tick())
    build.documents = FrozenDocuments(OrgdocsSource().snapshot(build.store, build.now))
    rows = plan_obligations(build.store, build.now).rows
    declared = {row.measure: row for row in build.profile.matrix}
    if unknown := sorted(set(declared) - {r.measure_id for r in rows}):
        raise BenchError([f"filas de `matrix` fuera de la Declaración: {', '.join(unknown)}"])
    settings = {r.measure_id: _row_settings(build, r.measure_id, declared) for r in rows}
    for measure, row in settings.items():
        owner = build.person(row.owner)
        set_entry(
            build.store,
            build.tick(),
            measure,
            owner_name=owner.name,
            owner_role=owner.roles[0].value,
            every_days=row.review_every_days,
        )
    for measure, link in _links(build):
        add_link(build.store, build.tick(), measure, link, build.documents)
    for spec in rows:
        _review(build, settings[spec.measure_id], spec.kind is RowKind.SELECTED)


def _row_settings(build: Build, measure: str, declared: dict[str, MatrixRow]) -> MatrixRow:
    if measure in declared:
        return declared[measure]
    defaults = build.profile.matrix_defaults
    if defaults is None:
        raise BenchError([f"{measure}: sin fila en `matrix` y sin `matrix_defaults`"])
    return MatrixRow(
        measure=measure,
        owner=defaults.owner,
        review_every_days=defaults.review_every_days,
        reviewed_by=defaults.reviewed_by,
        assessment=MeasureAssessment.COMPLIANT,
        conclusion=defaults.conclusion,
    )


def _links(build: Build) -> list[tuple[str, CheckLink | ManualEvidenceLink | MatrixDocumentLink]]:
    """Los enlaces del perfil con los ids reales: una recogida cuenta por su check."""
    links: list[tuple[str, CheckLink | ManualEvidenceLink | MatrixDocumentLink]] = []
    drill = build.profile.incident_drill
    for link in drill.links if drill else []:
        evidence_id = build.evidence_ids[drill.alias]
        links.append(
            (link.measure, ManualEvidenceLink(evidence_id=evidence_id, covers=link.covers))
        )
    for evidence in build.profile.evidence:
        for link in evidence.links:
            if evidence.source is EvidenceSource.COLLECTED:
                if link.check is None:
                    raise BenchError(
                        [f"{evidence.alias}: un enlace de una recogida nombra su `check`"]
                    )
                links.append((link.measure, CheckLink(check_id=link.check, covers=link.covers)))
            elif link.check is not None:
                raise BenchError([f"{evidence.alias}: una evidencia manual no lleva `check`"])
            else:
                evidence_id = build.evidence_ids[evidence.alias]
                links.append(
                    (link.measure, ManualEvidenceLink(evidence_id=evidence_id, covers=link.covers))
                )
    for link in build.profile.document_links:
        links.append((link.measure, MatrixDocumentLink(document=link.document, covers=link.covers)))
    return links


def _review(build: Build, row: MatrixRow, applicable: bool) -> None:
    if problems := _try_review(build, row, applicable):
        raise BenchError([f"{row.measure}: {problem}" for problem in problems])


def review_again(build: Build, measure: str) -> bool:
    """Vuelve a revisar `measure` como dice el perfil. Si el producto no admite esa revisión
    (p. ej. `COMPLIANT` con una evidencia caducada), no registra nada y la anterior sigue."""
    plan = plan_obligations(build.store, build.tick())
    spec = next((r for r in plan.rows if r.measure_id == measure), None) if plan.current else None
    if spec is None:
        return False
    declared = {row.measure: row for row in build.profile.matrix}
    row = _row_settings(build, measure, declared)
    return not _try_review(build, row, spec.kind is RowKind.SELECTED)


def _try_review(build: Build, row: MatrixRow, applicable: bool) -> tuple[str, ...]:
    reviewer = build.person(row.reviewed_by)
    role = next((r for r in reviewer.roles if r in SELF_ASSESSORS), None)
    if role is None:
        allowed = ", ".join(sorted(r.value for r in SELF_ASSESSORS))
        raise BenchError([f"{row.measure}: {reviewer.id} no revisa medidas (roles: {allowed})"])
    now = build.tick()
    plan = prepare_review(
        build.store,
        now,
        row.measure,
        by=reviewer.name,
        role=role.value,
        conclusion=row.conclusion or "Revisada.",
        assessment=row.assessment.value if applicable else None,
        checks=StoredCheckResults(reference_now=now),
        documents=build.documents,
    )
    if plan.problems:
        return plan.problems
    record_review(build.store, plan)
    return ()


def _dossier(build: Build) -> None:
    """Como `assessment dossier generate`: instantánea, `dossier.json` y `dossier.md`."""
    now = build.tick()
    snapshot = build_snapshot(
        build.store, current_inputs(now), checks=StoredCheckResults(reference_now=now)
    )
    build.dossier = save_snapshot(build.store, snapshot)
    save_markdown(build.store, build.dossier, render_snapshot_md(snapshot))


BASE_STEPS: tuple[tuple[str, Step], ...] = (
    ("alcance", _scope),
    ("categorización", _categorization),
    ("aplicabilidad", _applicability),
    ("evidencias", _evidence),
    ("org-docs", _orgdocs),
    ("simulacro", _drill),
    ("checks", _checks),
    ("matriz", _matrix),
)
"""Todo menos la instantánea: el estado sobre el que un caso aplica sus mutaciones."""
DOSSIER_STEP: tuple[str, Step] = ("expediente", _dossier)
STEPS: tuple[tuple[str, Step], ...] = (*BASE_STEPS, DOSSIER_STEP)


@dataclass(frozen=True)
class Inspection:
    """Lo que `benchmark build` enseña de un expediente construido; no se envía nada."""

    dossier: str
    payload_sha256: str | None
    """None en un caso solo determinista: no se prepara para juicio."""
    integrity: list[str]
    findings: list[Finding]


def inspect(build: Build) -> Inspection:
    """La capa determinista de la pre-auditoría y la huella del payload, como en
    `preaudit run` y `preaudit prepare`."""
    assert build.dossier is not None, "la construcción termina en la instantánea"
    integrity = dossier_problems(build.store, build.dossier)
    findings = deterministic_findings(
        load_snapshot(build.store, build.dossier), integrity=integrity
    )
    payload = build_payload(build.store, build.dossier, load_pack()) if build.judgment else None
    return Inspection(build.dossier, payload and payload.sha256, integrity, findings)


def build_profile(
    profile: Profile,
    files: Path,
    destination: Path,
    *,
    seed: str | None = None,
    steps: tuple[tuple[str, Step], ...] = STEPS,
) -> Build:
    """Construye el workspace de `profile` en `destination` (nuevo o vacío).

    `seed` siembra los ids de evidencia (por defecto, el id del perfil). Lanza `BenchError` con
    el paso y el motivo si el perfil no se puede construir.
    """
    if destination.exists() and any(destination.iterdir()):
        raise BenchError([f"perfil {profile.id}: el destino no está vacío"])
    destination.mkdir(parents=True, exist_ok=True)
    build = Build(profile, files, destination, seeded_ids(seed or profile.id))
    for name, step in steps:
        run_step(build, name, step)
    return build


def run_step(build: Build, name: str, step: Step) -> None:
    """Ejecuta `step`; un rechazo del producto sale como `BenchError` con el paso y el motivo."""
    profile = build.profile
    try:
        step(build)
    except BenchError as exc:
        raise _failed(profile, name, exc.problems) from None
    except (
        Rejected,
        ScopeApprovalRejected,
        EvidenceImportError,
        DocumentApprovalRejected,
        MatrixRejected,
        DossierRejected,
        IncidentRejected,
    ) as exc:
        errors = getattr(exc, "errors", None) or [str(exc)]
        raise _failed(profile, name, errors) from None
    except ScopeNotApproved as exc:
        state = exc.status.state.value
        raise _failed(profile, name, [f"el alcance no está aprobado ({state})"]) from None
    except CategorizationNotCurrent as exc:
        state = exc.status.state.value
        problems = [f"la categorización no está vigente ({state})", *exc.status.errors]
        raise _failed(profile, name, problems) from None
    except ValidationError as exc:
        raise _failed(profile, name, describe_validation(name, exc)) from None


def build_bench_profile(
    root: Path,
    profile_id: str,
    destination: Path,
    *,
    seed: str | None = None,
    steps: tuple[tuple[str, Step], ...] = STEPS,
) -> Build:
    """Carga `profiles/<profile_id>` de un banco y lo construye."""
    profile = load_profile(root, profile_id)
    files = profile_files_dir(root, profile_id)
    return build_profile(profile, files, destination, seed=seed, steps=steps)


def _failed(profile: Profile, step: str, problems: list[str]) -> BenchError:
    return BenchError([f"perfil {profile.id} › {step}: {p}" for p in problems])
