"""Paquete por medida para el auditor: metadatos de la instantánea, nunca los artefactos.

`build_package` solo lee la instantánea; no evalúa nada de nuevo. Cada medida es un fichero y
`index.json` es el manifiesto cerrado: la huella de cada fichero y las claves y huellas de los
manifiestos y artefactos que el paquete cita. `verify_package` los contrasta con el workspace:
cualquier ausencia o cambio es un problema visible. Los errores nombran rutas, nunca valores.
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from ens_evidence.assessment.dossier.model import (
    COMMITMENT,
    DossierSnapshot,
    snapshot_export,
)
from ens_evidence.assessment.dossier.privacy import export_problems, secret_problems
from ens_evidence.assessment.dossier.render import snapshot_sha256
from ens_evidence.assessment.dossier.store import (
    ASSESSMENTS_PREFIX,
    DossierUnreadable,
    load_snapshot,
    stored_snapshot_sha256,
)
from ens_evidence.checks.store import CHECKS_PREFIX
from ens_evidence.evidence.layout import artifact_key, manifest_key, sha256_hex
from ens_evidence.evidence.verify import VerifyReport, verify_one
from ens_evidence.matrix import MatrixExport
from ens_evidence.matrix.render import ExportedRow
from ens_evidence.models.contracts import Strict
from ens_evidence.workspace import NotFound, WorkspaceStore

PACKAGE_DIR = "package"
INDEX_NAME = "index.json"


class PackageRejected(Exception):
    """El paquete no se genera o no se guarda; `errors` dice por qué (rutas, nunca valores)."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


class _Entry(Strict):
    file: str
    sha256: str


class _Reference(Strict):
    evidence_id: str
    key: str
    sha256: str


class PackageIndex(Strict):
    commitment: str
    internal_review: str | None
    snapshot_sha256: str
    measures: list[_Entry]
    manifests: list[_Reference]
    artifacts: list[_Reference]


@dataclass(frozen=True)
class Package:
    """Nombre relativo de cada fichero (incluido `index.json`) y sus bytes."""

    files: dict[str, bytes]


def _json(document: Any) -> bytes:
    return (json.dumps(document, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode()


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _source_digests(snapshot: DossierSnapshot) -> dict[str, str]:
    return {s.key: s.sha256 for section in snapshot.sections for s in section.sources}


def _row_evidence_ids(row: ExportedRow, export: MatrixExport) -> list[str]:
    checks = {c.check_id: c for c in export.sources.checks}
    ids = {link.target for link in row.manual if link.kind == "evidence"}
    if row.last_review is not None:
        ids |= {e.evidence_id for o in row.last_review.obligations for e in o.evidence}
    for link in row.technical:
        if link.target in checks:
            ids |= set(checks[link.target].evidence_ids)
    return sorted(ids)


def _measure_file(
    snapshot: DossierSnapshot, export: MatrixExport, row: ExportedRow, digests: dict[str, str]
) -> tuple[dict, list[dict]]:
    """Contenido de la medida y las evidencias que cita (manifiesto y artefacto)."""
    known = {e.id: e for e in export.sources.evidence}
    evidence = [
        {
            "evidence_id": known[i].id,
            "manifest_key": known[i].manifest_key,
            "manifest_sha256": digests.get(known[i].manifest_key),
            "artifact_key": artifact_key(known[i].artifact_sha256),
            "artifact_sha256": known[i].artifact_sha256,
            "source": known[i].source,
            "collected_at": known[i].collected_at.isoformat(),
            "valid_until": known[i].valid_until and known[i].valid_until.isoformat(),
        }
        for i in _row_evidence_ids(row, export)
        if i in known
    ]
    checks = {c.check_id: c for c in export.sources.checks}
    executions = [
        {
            "check_id": link.target,
            "connector": checks[link.target].connector,
            "last_run_at": checks[link.target].last_run_at
            and checks[link.target].last_run_at.isoformat(),
            "runs": [
                {"key": key, "sha256": digest}
                for key, digest in sorted(digests.items())
                if checks[link.target].connector is not None
                and key.startswith(f"{CHECKS_PREFIX}/{checks[link.target].connector}/")
            ],
        }
        for link in row.technical
        if link.target in checks
    ]
    documents = {d.name: d for d in export.sources.documents}
    review = row.last_review
    document = {
        "measure_id": row.measure_id,
        "name": row.name,
        "kind": row.kind.value,
        "category": snapshot.category.value,
        "internal_review": snapshot.notice,
        "commitment": COMMITMENT,
        "obligations": [o.model_dump(mode="json") for o in row.obligations],
        "compensated_clauses": row.compensated_clauses,
        "links": [link.model_dump(mode="json") for link in [*row.technical, *row.manual]],
        "review": review
        and {**review.model_dump(mode="json"), "file_sha256": digests.get(review.key)},
        "evidence": evidence,
        "check_executions": executions,
        "documents": [
            {"name": name, "document_sha256": documents[name].document_sha256}
            for name in sorted(
                {link.target for link in row.manual if link.kind == "org_document"} & set(documents)
            )
        ],
        "blockers": [b.model_dump(mode="json") for b in row.blockers],
    }
    return document, evidence


def _leaks(name: str, document: Any) -> list[str]:
    where = f"{name}: "
    found = secret_problems(document, where) + export_problems(where, document)
    return sorted(set(found))


def build_package(snapshot: DossierSnapshot) -> Package:
    export = snapshot_export(snapshot)
    digests = _source_digests(snapshot)
    files: dict[str, bytes] = {}
    manifests: dict[str, _Reference] = {}
    artifacts: dict[str, _Reference] = {}
    problems: list[str] = []
    for row in sorted(export.rows, key=lambda r: r.measure_id):
        document, evidence = _measure_file(snapshot, export, row, digests)
        name = f"{row.measure_id}.json"
        files[name] = _json(document)
        problems += _leaks(name, document)
        if row.last_review is not None and row.last_review.key not in digests:
            problems.append(f"{name}: la revisión {row.last_review.key} no figura en las fuentes")
        for item in evidence:
            if item["manifest_sha256"] is None:
                problems.append(
                    f"{name}: el manifiesto {item['manifest_key']} no figura en fuentes"
                )
                continue
            manifests[item["evidence_id"]] = _Reference(
                evidence_id=item["evidence_id"],
                key=item["manifest_key"],
                sha256=item["manifest_sha256"],
            )
            artifacts[item["evidence_id"]] = _Reference(
                evidence_id=item["evidence_id"],
                key=item["artifact_key"],
                sha256=item["artifact_sha256"],
            )
    if problems:
        raise PackageRejected(sorted(set(problems)))
    index = PackageIndex(
        commitment=COMMITMENT,
        internal_review=snapshot.notice,
        snapshot_sha256=snapshot_sha256(snapshot),
        measures=[_Entry(file=name, sha256=_digest(data)) for name, data in files.items()],
        manifests=[manifests[i] for i in sorted(manifests)],
        artifacts=[artifacts[i] for i in sorted(artifacts)],
    )
    files[INDEX_NAME] = _json(index.model_dump(mode="json"))
    return Package(files)


def _prefix(dossier: str) -> str:
    return f"{ASSESSMENTS_PREFIX}/{dossier}/{PACKAGE_DIR}"


def has_package(store: WorkspaceStore, dossier: str) -> bool:
    """Hay algo bajo `package/`, aunque falte el índice: un paquete sin índice es incompleto."""
    return bool(store.list(_prefix(dossier)))


def save_package(store: WorkspaceStore, dossier: str, snapshot: DossierSnapshot) -> None:
    """Solo adición: `AlreadyExists` si ya hay un paquete. El índice se escribe el último."""
    if stored_snapshot_sha256(store, dossier) != snapshot_sha256(snapshot):
        raise PackageRejected([f"{dossier}: la instantánea no es la guardada"])
    package = build_package(snapshot)
    for name, data in sorted(package.files.items(), key=lambda item: item[0] == INDEX_NAME):
        store.create_bytes(f"{_prefix(dossier)}/{name}", data)


def _read(store: WorkspaceStore, key: str, problems: list[str]) -> bytes | None:
    try:
        return store.read_bytes(key)
    except NotFound:
        problems.append(f"{key}: no existe")
        return None


def _verify_reference(
    store: WorkspaceStore, kind: str, reference: _Reference, problems: list[str]
) -> None:
    data = _read(store, reference.key, problems)
    if data is not None and sha256_hex(data) != reference.sha256:
        problems.append(f"{reference.key}: el {kind} ha cambiado desde que se generó el paquete")


def _differences_from_rebuild(
    store: WorkspaceStore, dossier: str, prefix: str, known: list[str]
) -> list[str]:
    """Los ficheros guardados son byte a byte los que da `build_package` del expediente."""
    try:
        expected = build_package(load_snapshot(store, dossier)).files
    except PackageRejected as error:
        return [f"{prefix}: el paquete no se puede reconstruir ({'; '.join(error.errors)})"]
    found: list[str] = []
    for name, data in expected.items():
        key = f"{prefix}/{name}"
        if any(problem.startswith(f"{key}:") for problem in known):
            continue
        try:
            stored = store.read_bytes(key)
        except NotFound:
            continue
        if stored != data:
            found.append(f"{key}: no coincide con el que sale del expediente")
    return found


def verify_package(store: WorkspaceStore, dossier: str) -> VerifyReport:
    prefix = _prefix(dossier)
    problems: list[str] = []
    try:
        expected = stored_snapshot_sha256(store, dossier)
    except DossierUnreadable as error:
        return VerifyReport(0, error.problems)
    raw = _read(store, f"{prefix}/{INDEX_NAME}", problems)
    if raw is None:
        return VerifyReport(0, problems)
    try:
        index = PackageIndex.model_validate_json(raw)
    except ValidationError as error:
        loc = [".".join(map(str, e["loc"])) for e in error.errors()]
        return VerifyReport(1, [f"{prefix}/{INDEX_NAME}: índice ilegible ({', '.join(loc)})"])
    if index.snapshot_sha256 != expected:
        problems.append(f"{prefix}/{INDEX_NAME}: el paquete no corresponde al expediente guardado")
    listed = {INDEX_NAME, *(m.file for m in index.measures)}
    problems += [
        f"{key}: fichero ajeno al índice del paquete"
        for key in store.list(prefix)
        if key.removeprefix(prefix + "/") not in listed
    ]
    cited: set[str] = set()
    for entry in index.measures:
        data = _read(store, f"{prefix}/{entry.file}", problems)
        if data is None:
            continue
        if _digest(data) != entry.sha256:
            problems.append(f"{prefix}/{entry.file}: ha cambiado desde que se generó el paquete")
            continue
        cited |= {e["evidence_id"] for e in json.loads(data)["evidence"]}
    problems += _differences_from_rebuild(store, dossier, prefix, problems)
    listed_ids = {m.evidence_id for m in index.manifests}
    problems += [
        f"{prefix}/{INDEX_NAME}: {i} lo cita una medida y falta en el índice"
        for i in sorted(cited - listed_ids)
    ]
    for reference in index.manifests:
        _verify_reference(store, "manifiesto", reference, problems)
        if reference.key != manifest_key(reference.evidence_id):
            problems.append(f"{reference.key}: no es el manifiesto de {reference.evidence_id}")
        problems += verify_one(store, reference.evidence_id).problems
    for reference in index.artifacts:
        _verify_reference(store, "artefacto", reference, problems)
    return VerifyReport(1 + len(index.measures), sorted(set(problems)))


__all__ = [
    "Package",
    "PackageIndex",
    "PackageRejected",
    "build_package",
    "has_package",
    "save_package",
    "verify_package",
]
