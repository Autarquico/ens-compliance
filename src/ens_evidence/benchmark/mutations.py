"""Mutaciones de un caso: el defecto se siembra como lo haría una persona sobre el workspace.

Cada operación de la lista cerrada (`model.Mutation`) usa la API de dominio del producto, o edita
los YAML igual que el usuario y vuelve a aprobar con la función del producto. Se aplican en orden,
sobre el perfil ya construido (`BASE_STEPS`) y antes de generar el expediente: no hay fase
configurable.

Tras cada operación, quien revisa cada medida afectada la revisa otra vez como dice el perfil. Si
el producto no admite esa revisión (`COMPLIANT` con una evidencia caducada, desenlazada o un check
en `FAIL`), no se registra y la anterior queda desfasada: así lo que siembra el caso es el defecto
y no una revisión olvidada. `tamper_artifact` no afecta a ninguna: se altera a espaldas de todos.
"""

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from ens_evidence.applicability import approve_declaration
from ens_evidence.benchmark.build import (
    BASE_STEPS,
    DOSSIER_STEP,
    TICK,
    Build,
    approve_documents,
    build_bench_profile,
    review_again,
    run_step,
    seeded_ids,
)
from ens_evidence.benchmark.cases import BenchError, case_files_dir
from ens_evidence.benchmark.model import (
    Case,
    CollectedOverride,
    Compensate,
    DeclareNotApplicable,
    EvidenceSource,
    ExpireEvidence,
    Mutation,
    ProfileEvidence,
    ReplaceEvidenceText,
    SetAnswer,
    TamperArtifact,
    TargetKind,
    UnlinkDocument,
    UnlinkEvidence,
)
from ens_evidence.catalog.controls import load_measures, save_measures
from ens_evidence.checks.engine import OutcomeKind, run_all
from ens_evidence.evidence.importer import import_collected, import_manual
from ens_evidence.evidence.manifests import load_manifest_by_id
from ens_evidence.matrix import ManualEvidenceLink, add_link, remove_link
from ens_evidence.matrix.links import load_matrix
from ens_evidence.models.contracts import CollectionResult
from ens_evidence.models.enums import Applicability, Role
from ens_evidence.models.preaudit import Ref, RefKind
from ens_evidence.models.readiness import Compensation
from ens_evidence.orgdocs import AnswersInvalid, build_documents
from ens_evidence.preaudit.pack import load_pack
from ens_evidence.preaudit.payload import build_payload
from ens_evidence.scope.messages import describe_validation
from ens_evidence.yaml_unique import safe_load_unique

ANSWERS_FILE = "org/answers.yaml"
TAMPER_MARK = b"\n(alterado a mano tras importarlo)\n"


def build_case(root: Path, case: Case, destination: Path) -> Build:
    """El perfil base de `case` (sembrado con el id del perfil, el mismo para todos sus casos,
    así se puede cachear); sus mutaciones, sembradas con la clave del caso; y el expediente."""
    if case.profile is None:
        raise BenchError([f"{case.key}: las bases `workspace:` aún no se construyen"])
    base = build_bench_profile(root, case.profile, destination, steps=BASE_STEPS)
    return finish_case(base, case, case_files_dir(root, case))


def finish_case(base: Build, case: Case, files: Path) -> Build:
    """Aplica las mutaciones de `case` sobre `base` (un perfil sin expediente, o una copia suya)
    y lo genera. Los ids que emite el caso salen de su clave.

    `files` es el directorio de ficheros del caso (`replace_evidence_text`, `collected_override`).
    """
    base.id_factory = seeded_ids(case.key)
    base.judgment = case.judgment
    apply_mutations(base, case.mutations, files)
    run_step(base, *DOSSIER_STEP)
    return base


def apply_mutations(build: Build, mutations: list[Mutation], files: Path) -> None:
    for index, mutation in enumerate(mutations):
        operation = OPERATIONS.get(type(mutation))
        if operation is None:
            raise BenchError([f"mutations[{index}]: operación desconocida {mutation!r}"])
        run_step(
            build,
            f"mutations[{index}] ({mutation.op})",
            lambda b, m=mutation, op=operation: _review_affected(b, op(b, m, files)),
        )


def _review_affected(build: Build, measures: list[str]) -> None:
    for measure in sorted(set(measures)):
        review_again(build, measure)


# --- evidencias -------------------------------------------------------------------------------


def _replace_evidence_text(build: Build, mutation: ReplaceEvidenceText, files: Path) -> list[str]:
    """Otra versión del fichero, importada como evidencia nueva y enlazada en lugar de la vieja."""
    evidence = _manual_evidence(build, mutation.alias)
    data = _case_file(files, mutation.file)
    return _reimport(build, evidence, data, valid_until=evidence.valid_until, collected_at=None)


def _expire_evidence(build: Build, mutation: ExpireEvidence, files: Path) -> list[str]:
    """La misma evidencia, reimportada con `valid_until` en el pasado (recogida un minuto antes)."""
    evidence = _manual_evidence(build, mutation.alias)
    old = load_manifest_by_id(build.store, build.evidence_ids[evidence.alias])
    data = build.store.read_bytes(old.artifact.key)
    collected_at = mutation.valid_until - TICK
    return _reimport(
        build, evidence, data, valid_until=mutation.valid_until, collected_at=collected_at
    )


def _reimport(
    build: Build,
    evidence: ProfileEvidence,
    data: bytes,
    *,
    valid_until: datetime | None,
    collected_at: datetime | None,
) -> list[str]:
    """La evidencia nueva, en cada enlace de la matriz que citaba la anterior; sus medidas."""
    assert evidence.asset is not None  # el builder exige `asset` a una evidencia manual
    old_id = build.evidence_ids[evidence.alias]
    manifest = import_manual(
        build.store,
        data,
        asset_id=evidence.asset,
        submitted_by=build.signer(Role.SECURITY_OFFICER),
        now=build.tick(),
        collected_at=collected_at,
        valid_until=valid_until,
        id_factory=build.id_factory,
    )
    build.evidence_ids[evidence.alias] = manifest.id
    relinked = []
    for entry in load_matrix(build.store).entries:
        for link in entry.links:
            if link.target == ("evidence", old_id):
                remove_link(build.store, build.tick(), entry.id, "evidence", old_id)
                replacement = ManualEvidenceLink(evidence_id=manifest.id, covers=link.covers)
                add_link(build.store, build.tick(), entry.id, replacement)
                relinked.append(entry.id)
    return relinked


def _unlink_evidence(build: Build, mutation: UnlinkEvidence, files: Path) -> list[str]:
    """`matrix unlink` de la evidencia; una recogida se desenlaza por su check."""
    evidence = _evidence(build, mutation.alias)
    if evidence is not None and evidence.source is EvidenceSource.COLLECTED:
        checks = [
            link.check for link in evidence.links if link.measure == mutation.measure and link.check
        ]
        if not checks:
            raise BenchError([f"{mutation.alias} no está enlazada en {mutation.measure}"])
        for check in checks:
            remove_link(build.store, build.tick(), mutation.measure, "check", check)
        return [mutation.measure]
    evidence_id = _evidence_id(build, mutation.alias)
    remove_link(build.store, build.tick(), mutation.measure, "evidence", evidence_id)
    return [mutation.measure]


def _unlink_document(build: Build, mutation: UnlinkDocument, files: Path) -> list[str]:
    remove_link(build.store, build.tick(), mutation.measure, "org_document", mutation.ref)
    return [mutation.measure]


def _collected_override(build: Build, mutation: CollectedOverride, files: Path) -> list[str]:
    """Una recogida nueva del mismo activo con otro contenido, y `check --all` sobre ella."""
    evidence = _evidence(build, mutation.alias)
    if evidence is None or evidence.source is not EvidenceSource.COLLECTED:
        raise BenchError([f"{mutation.alias} no es una recogida; usa replace_evidence_text"])
    previous = load_manifest_by_id(build.store, build.evidence_ids[evidence.alias])
    try:
        result = CollectionResult.model_validate_json(_case_file(files, mutation.file))
    except ValidationError as exc:
        raise BenchError(describe_validation(f"files/{mutation.file}", exc)) from None
    if result.asset_id != previous.asset_id:
        raise BenchError(
            [f"{mutation.file}: la recogida es de {result.asset_id}, no de {previous.asset_id}"]
        )
    manifest = import_collected(
        build.store,
        result,
        collector_version=previous.collector.version if previous.collector else "benchmark",
        now=build.tick(),
        id_factory=build.id_factory,
    )
    build.evidence_ids[evidence.alias] = manifest.id
    for outcome in run_all(build.store, build.tick()):
        if outcome.kind in (OutcomeKind.REJECTED, OutcomeKind.HISTORY_UNREADABLE):
            raise BenchError([f"{outcome.connector}: {outcome.kind.value}", *outcome.detail])
    return _linked_measures(build, "check")


def _tamper_artifact(build: Build, mutation: TamperArtifact, files: Path) -> list[str]:
    """Edita a mano los bytes guardados de la evidencia: su huella deja de cuadrar."""
    manifest = load_manifest_by_id(build.store, _evidence_id(build, mutation.alias))
    key = manifest.artifact.key
    build.store.write_bytes(key, build.store.read_bytes(key) + TAMPER_MARK)
    return []


def _linked_measures(build: Build, kind: str) -> list[str]:
    """Las medidas con algún enlace de `kind` en la matriz."""
    entries = load_matrix(build.store).entries
    return [e.id for e in entries if any(link.kind == kind for link in e.links)]


def _evidence(build: Build, alias: str) -> ProfileEvidence | None:
    return next((e for e in build.profile.evidence if e.alias == alias), None)


def _manual_evidence(build: Build, alias: str) -> ProfileEvidence:
    evidence = _evidence(build, alias)
    if evidence is None or evidence.source is not EvidenceSource.MANUAL:
        raise BenchError([f"{alias} no es una evidencia manual del perfil"])
    return evidence


def _evidence_id(build: Build, alias: str) -> str:
    if alias not in build.evidence_ids:
        raise BenchError([f"la evidencia {alias} no está en el perfil"])
    return build.evidence_ids[alias]


def _case_file(files: Path, name: str) -> bytes:
    path = files / name
    if not path.is_file():
        raise BenchError([f"falta el fichero del caso {name}"])
    return path.read_bytes()


# --- org-docs ---------------------------------------------------------------------------------


def _set_answer(build: Build, mutation: SetAnswer, files: Path) -> list[str]:
    """Edita `org/answers.yaml`, `docs build` y vuelve a aprobar los seis documentos."""
    answers = safe_load_unique(build.store.read_text(ANSWERS_FILE))
    _assign(answers, mutation.key.split("."), mutation.value)
    text = yaml.safe_dump(answers, allow_unicode=True, sort_keys=False, width=100)
    build.store.write_text(ANSWERS_FILE, text)
    try:
        build_documents(build.store)
    except AnswersInvalid as exc:
        raise BenchError(exc.report.errors()) from None
    approve_documents(build)
    build.documents = None  # la instantánea de org-docs que leyó la matriz ya no es la vigente
    return _linked_measures(build, "org_document")


def _assign(tree: Any, path: list[str], value: object) -> None:
    head, *rest = path
    if isinstance(tree, list) and head.isdigit() and int(head) < len(tree):
        index: int | str = int(head)
    elif isinstance(tree, dict) and (head in tree or not rest):
        index = head
    else:
        raise BenchError([f"set_answer: la respuesta «{head}» no existe en {ANSWERS_FILE}"])
    if rest:
        _assign(tree[index], rest, value)
    else:
        tree[index] = value


# --- Declaración de aplicabilidad -------------------------------------------------------------


def _declare_not_applicable(build: Build, mutation: DeclareNotApplicable, files: Path) -> list[str]:
    return _redeclare(
        build,
        mutation.measure,
        {
            "applicability": Applicability.NOT_APPLICABLE,
            "justification": mutation.justification,
            "proposed": False,
            "chosen_reinforcements": [],
            "compensation": None,
        },
    )


def _compensate(build: Build, mutation: Compensate, files: Path) -> list[str]:
    compensation = Compensation(
        measures=mutation.measures,
        replaces=mutation.replaces,
        justification=mutation.justification,
    )
    return _redeclare(build, mutation.measure, {"compensation": compensation, "proposed": False})


def _redeclare(build: Build, measure_id: str, update: dict[str, object]) -> list[str]:
    """Edita la medida en `controls/measures.yaml` y vuelve a firmar la Declaración."""
    document = load_measures(build.store)
    assert document is not None  # el perfil base ya firmó la Declaración
    if measure_id not in {m.id for m in document.measures}:
        raise BenchError([f"{measure_id} no está en la Declaración"])
    measures = [
        m.model_validate({**m.model_dump(), **update}) if m.id == measure_id else m
        for m in document.measures
    ]
    save_measures(build.store, document.model_copy(update={"measures": measures}))
    approve_declaration(build.store, build.signer(Role.SECURITY_OFFICER), build.tick())
    return [measure_id]


Operation = Callable[[Build, Any, Path], list[str]]
OPERATIONS: dict[type, Operation] = {
    ReplaceEvidenceText: _replace_evidence_text,
    ExpireEvidence: _expire_evidence,
    UnlinkEvidence: _unlink_evidence,
    UnlinkDocument: _unlink_document,
    SetAnswer: _set_answer,
    DeclareNotApplicable: _declare_not_applicable,
    Compensate: _compensate,
    CollectedOverride: _collected_override,
    TamperArtifact: _tamper_artifact,
}
"""La lista cerrada de `model.Mutation`, operación por operación."""


# --- objetivos de los defectos ----------------------------------------------------------------


def resolve_targets(build: Build, case: Case) -> dict[str, Ref | None]:
    """El objetivo de cada defecto con el id real que citan los hallazgos (`None`: ausencia).

    Evidencia: el id vigente del alias (tras las mutaciones); documento: su ref; justificación:
    la sección `justification:…` de la medida en el payload."""
    targets: dict[str, Ref | None] = {}
    for defect in case.defects:
        target = defect.target
        match target.kind:
            case TargetKind.EVIDENCE:
                assert target.alias is not None
                targets[defect.id] = Ref(
                    kind=RefKind.EVIDENCE, id=_evidence_id(build, target.alias)
                )
            case TargetKind.DOCUMENT:
                assert target.ref is not None
                targets[defect.id] = Ref(kind=RefKind.DOCUMENT, id=target.ref)
            case TargetKind.JUSTIFICATION:
                targets[defect.id] = _justification(build, defect.id, defect.measure)
            case TargetKind.NONE:
                targets[defect.id] = None
    return targets


def _justification(build: Build, defect: str, measure: str) -> Ref:
    assert build.dossier is not None, "los objetivos se resuelven sobre el expediente generado"
    data = build_payload(build.store, build.dossier, load_pack()).data
    found = [j["id"] for j in data["dossier"]["justifications"] if j["measure"] == measure]
    if len(found) != 1:
        raise BenchError(
            [f"defecto {defect}: {len(found)} justificaciones de {measure} en el payload, no 1"]
        )
    return Ref(kind=RefKind.SECTION, id=found[0])
