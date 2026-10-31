"""Entradas de la matriz y sus enlaces: `init`, `set`, `link` y `unlink`.

Aquí viven las reglas de las órdenes `matrix …`; la CLI solo las presenta. Todo cambio pasa por
`controls/matrix.yaml` (nunca por `measures.yaml`, que ata la Declaración, ADR 0011).
"""

from dataclasses import dataclass
from datetime import datetime

from pydantic import ValidationError

from ens_evidence.catalog import load_catalog
from ens_evidence.catalog.controls import CatalogVersion, current_catalog_version
from ens_evidence.evidence.importer import (
    INCIDENT_HISTORY_SOURCE,
    INCIDENT_RECORD_SOURCE,
    MANUAL_SOURCE,
)
from ens_evidence.incidents import verify_history
from ens_evidence.matrix.documents import DocumentsSnapshot, OrgDocumentSource, documents_snapshot
from ens_evidence.matrix.evidence import verified_manifest
from ens_evidence.matrix.history import register_entry, register_matrix
from ens_evidence.matrix.messages import (
    DECLARATION_REASONS,
    INCIDENT_RECORD_NOT_LINKABLE,
    blocker_text,
)
from ens_evidence.matrix.model import (
    MAX_REVIEW_EVERY_DAYS,
    CheckLink,
    DocumentLink,
    Link,
    ManualEvidenceLink,
    MatrixEntry,
    MatrixFile,
    Owner,
)
from ens_evidence.matrix.obligations import (
    MatrixRowSpec,
    ObligationPlan,
    expand_covers,
    plan_obligations,
)
from ens_evidence.matrix.policy import SourcePolicy, admits, source_policy
from ens_evidence.matrix.store import read_matrix, save_matrix
from ens_evidence.matrix.technical import KNOWN_CHECKS
from ens_evidence.models.contracts import ProvenanceKind
from ens_evidence.models.enums import Role
from ens_evidence.models.readiness import Blocker, BlockerKind
from ens_evidence.workspace import WorkspaceError, WorkspaceStore

EVIDENCE = "evidence"
CHECK = "check"
DOCUMENT = "org_document"
KIND_LABELS = {EVIDENCE: "evidencia", CHECK: "check", DOCUMENT: "documento"}


class MatrixRejected(Exception):
    """La orden no se aplica; `errors` dice por qué, una razón por línea."""

    def __init__(self, *errors: str) -> None:
        super().__init__("; ".join(errors))
        self.errors = list(errors)


@dataclass(frozen=True)
class InitOutcome:
    created: tuple[str, ...]
    kept: int
    extra: tuple[str, ...]
    file_catalog: CatalogVersion
    current_catalog: CatalogVersion

    @property
    def catalog_updated(self) -> bool:
        """`matrix.yaml` tenía otra versión del catálogo y ahora lleva la actual."""
        return self.file_catalog != self.current_catalog


@dataclass(frozen=True)
class LinkOutcome:
    """`declared`: lo que declara este enlace; `pending`: lo que ningún enlace declara."""

    entry: MatrixEntry
    declared: tuple[str, ...]
    pending: tuple[str, ...]
    removed: bool = False


def require_current(store: WorkspaceStore, now: datetime) -> ObligationPlan:
    plan = plan_obligations(store, now)
    if not plan.current:
        raise MatrixRejected(
            f"La Declaración de Aplicabilidad no está vigente: {DECLARATION_REASONS[plan.state]}; "
            "ver `applicability status`.",
            *plan.errors,
        )
    return plan


def load_matrix(store: WorkspaceStore) -> MatrixFile:
    matrix, errors = read_matrix(store)
    if errors:
        raise WorkspaceError("controls/matrix.yaml no es válido: " + "; ".join(errors))
    if matrix is None:
        raise MatrixRejected("Falta la matriz: ejecuta `matrix init`.")
    return matrix


def entry_of(matrix: MatrixFile, measure_id: str) -> MatrixEntry:
    found = next((e for e in matrix.entries if e.id == measure_id), None)
    if found is None:
        raise MatrixRejected(
            f"{measure_id} no tiene entrada en la matriz: no es una medida de la Declaración "
            "vigente o falta ejecutar `matrix init`."
        )
    return found


def row_of(plan: ObligationPlan, measure_id: str) -> MatrixRowSpec:
    row = plan.row(measure_id)
    if row is None:
        raise MatrixRejected(f"{measure_id} no es una medida de la Declaración de Aplicabilidad.")
    return row


def _replaced(matrix: MatrixFile, entry: MatrixEntry) -> MatrixFile:
    entries = [entry if e.id == entry.id else e for e in matrix.entries]
    return matrix.model_copy(update={"entries": entries})


def _commit(
    store: WorkspaceStore,
    now: datetime,
    matrix: MatrixFile,
    before: MatrixEntry,
    after: MatrixEntry,
) -> None:
    """Guarda la entrada y registra su estado en el historial. Si `before` (lo que había en
    `matrix.yaml`) no era el último estado registrado, se editó a mano: se registra también, para
    que un cambio posterior no lo borre del historial."""
    register_entry(store, before, now)
    save_matrix(store, _replaced(matrix, after))
    register_entry(store, after, now)


def init_matrix(store: WorkspaceStore, now: datetime) -> InitOutcome:
    """Una entrada por fila de la Declaración y el catálogo actual; conserva lo existente.

    Solo escribe si hay algo que cambiar. La Declaración vigente ya se firmó con el catálogo
    actual, así que alinear la versión de `matrix.yaml` con él no reabre ninguna revisión.
    """
    plan = require_current(store, now)
    current = current_catalog_version(load_catalog())
    matrix, errors = read_matrix(store)
    if errors:
        raise WorkspaceError("controls/matrix.yaml no es válido: " + "; ".join(errors))
    existing = matrix or MatrixFile(catalog_version=current)
    known = {e.id for e in existing.entries}
    wanted = [r.measure_id for r in plan.rows]
    created = tuple(i for i in wanted if i not in known)
    outcome = InitOutcome(
        created,
        len(known & set(wanted)),
        tuple(sorted(known - set(wanted))),
        existing.catalog_version,
        current,
    )
    if created or matrix is None or outcome.catalog_updated:
        entries = [*existing.entries, *(MatrixEntry(id=i) for i in created)]
        existing = existing.model_copy(update={"entries": entries, "catalog_version": current})
        save_matrix(store, existing)
    register_matrix(store, existing, now)
    return outcome


def parse_role(text: str) -> Role:
    try:
        return Role(text)
    except ValueError:
        raise MatrixRejected(
            f"Rol desconocido «{text}»; roles: {', '.join(r.value for r in Role)}."
        ) from None


def set_entry(
    store: WorkspaceStore,
    now: datetime,
    measure_id: str,
    *,
    owner_name: str | None = None,
    owner_role: str | None = None,
    every_days: int | None = None,
) -> MatrixEntry:
    errors: list[str] = []
    if (owner_name is None) != (owner_role is None):
        errors.append("--owner y --owner-role van juntos.")
    if owner_name is not None and not owner_name.strip():
        errors.append("--owner no puede estar vacío.")
    if every_days is not None and not 1 <= every_days <= MAX_REVIEW_EVERY_DAYS:
        errors.append(f"--every: de 1 a {MAX_REVIEW_EVERY_DAYS} días.")
    if owner_name is None and every_days is None:
        errors.append("Indica qué cambiar: --owner y --owner-role, o --every.")
    if errors:
        raise MatrixRejected(*errors)
    matrix = load_matrix(store)
    entry = entry_of(matrix, measure_id)
    update: dict = {}
    if owner_name is not None and owner_role is not None:
        update["owner"] = Owner(name=owner_name.strip(), role=parse_role(owner_role))
    if every_days is not None:
        update["review_every_days"] = every_days
    updated = entry.model_copy(update=update)
    _commit(store, now, matrix, entry, updated)
    return updated


def parse_covers(text: str) -> list[str]:
    tokens = [t.strip() for t in text.split(",")]
    if not text.strip() or not all(tokens):
        raise MatrixRejected("--covers: lista separada por comas, sin elementos vacíos.")
    return tokens


def build_link(kind: str, target: str, covers: list[str]) -> Link:
    try:
        if kind == EVIDENCE:
            return ManualEvidenceLink(evidence_id=target, covers=covers)
        if kind == CHECK:
            return CheckLink(check_id=target, covers=covers)
        return DocumentLink(document=target, covers=covers)
    except ValidationError as exc:
        raise MatrixRejected(
            *(str(e["msg"]).removeprefix("Value error, ") for e in exc.errors())
        ) from None


def _policy_error(row: MatrixRowSpec, policy: SourcePolicy) -> list[str]:
    return [
        blocker_text(
            Blocker(
                kind=BlockerKind.MISSING_EVIDENCE, measure_id=row.measure_id, detail=policy.detail
            )
        )
    ]


def _check_evidence(
    store: WorkspaceStore, row: MatrixRowSpec, link: ManualEvidenceLink
) -> list[str]:
    manifest = verified_manifest(store, link.evidence_id)
    if manifest is None:
        return [
            f"La evidencia {link.evidence_id} no existe o no pasa la verificación de "
            "integridad (`evidence verify`)."
        ]
    if manifest.source == INCIDENT_RECORD_SOURCE:
        return [INCIDENT_RECORD_NOT_LINKABLE.format(a=link.evidence_id)]
    policy = source_policy(row.measure_id)
    if policy and not admits(policy, manifest.source):
        return _policy_error(row, policy)
    if manifest.source == INCIDENT_HISTORY_SOURCE:
        problems = verify_history(store, manifest)
        if problems:
            return [f"El historial {link.evidence_id} no es válido:", *problems]
    elif manifest.provenance.kind is not ProvenanceKind.MANUAL or manifest.source != MANUAL_SOURCE:
        return [
            f"{link.evidence_id} no es una evidencia manual; la de un collector o de un "
            "endpoint cuenta por su check."
        ]
    return []


def _check_source(
    store: WorkspaceStore, row: MatrixRowSpec, link: Link, snapshot: DocumentsSnapshot | None
) -> list[str]:
    policy = source_policy(row.measure_id)
    match link:
        case ManualEvidenceLink():
            return _check_evidence(store, row, link)
        case CheckLink():
            if policy:
                return _policy_error(row, policy)
            if link.check_id not in KNOWN_CHECKS:
                return [
                    f"Check desconocido «{link.check_id}»; conocidos: "
                    f"{', '.join(sorted(KNOWN_CHECKS))}."
                ]
        case DocumentLink():
            if policy:
                return _policy_error(row, policy)
            assert snapshot is not None, "un enlace a documento se comprueba con su instantánea"
            if link.document not in snapshot.documents:
                return [
                    f"Documento desconocido «{link.document}»; conocidos: "
                    f"{', '.join(sorted(snapshot.documents))}."
                ]
    return []


def declared_units(row: MatrixRowSpec, entry: MatrixEntry) -> frozenset[str]:
    """Unidades que algún enlace declara cubrir (declaración, no validez: eso es `assess`)."""
    return frozenset().union(*(expand_covers(link.covers, row).units for link in entry.links))


def pending_units(row: MatrixRowSpec, entry: MatrixEntry) -> tuple[str, ...]:
    declared = declared_units(row, entry)
    return tuple(u for o in row.obligations for u in o.units if u not in declared)


def add_link(
    store: WorkspaceStore,
    now: datetime,
    measure_id: str,
    link: Link,
    documents: OrgDocumentSource | None = None,
) -> LinkOutcome:
    """Vincula la fuente; si ya estaba vinculada, une los `covers`."""
    plan = require_current(store, now)
    row = row_of(plan, measure_id)
    matrix = load_matrix(store)
    entry = entry_of(matrix, measure_id)
    expansion = expand_covers(link.covers, row)
    snapshot = documents_snapshot(store, now, documents) if isinstance(link, DocumentLink) else None
    errors = [*expansion.errors, *_check_source(store, row, link, snapshot)]
    if errors:
        raise MatrixRejected(*errors)
    previous = next((x for x in entry.links if x.target == link.target), None)
    if previous is not None:
        merged = [*previous.covers, *(c for c in link.covers if c not in previous.covers)]
        link = link.model_copy(update={"covers": merged})
    links = [x for x in entry.links if x.target != link.target]
    updated = entry.model_copy(update={"links": [*links, link]})
    _commit(store, now, matrix, entry, updated)
    return LinkOutcome(
        updated, tuple(sorted(expand_covers(link.covers, row).units)), pending_units(row, updated)
    )


def remove_link(
    store: WorkspaceStore,
    now: datetime,
    measure_id: str,
    kind: str,
    target: str,
    covers: list[str] | None = None,
) -> LinkOutcome:
    """Desvincula la fuente; con `covers`, solo esas coberturas (si no queda ninguna, el enlace)."""
    matrix = load_matrix(store)
    entry = entry_of(matrix, measure_id)
    found = next((x for x in entry.links if x.target == (kind, target)), None)
    if found is None:
        raise MatrixRejected(f"{measure_id} no tiene vinculado {target}.")
    remaining: list[str] = []
    if covers is not None:
        unknown = [c for c in covers if c not in found.covers]
        if unknown:
            raise MatrixRejected(
                f"{target} no cubre {', '.join(unknown)} en {measure_id} "
                f"(cubre {', '.join(found.covers)})."
            )
        remaining = [c for c in found.covers if c not in covers]
    links = [x for x in entry.links if x is not found]
    if remaining:
        links.append(found.model_copy(update={"covers": remaining}))
    updated = entry.model_copy(update={"links": links})
    _commit(store, now, matrix, entry, updated)
    plan = plan_obligations(store, now)
    row = plan.row(measure_id) if plan.current else None
    pending = pending_units(row, updated) if row else ()
    return LinkOutcome(updated, (), pending, removed=not remaining)


def link_label(link: Link) -> str:
    return source_label(*link.target)


def source_label(kind: str, target: str) -> str:
    """La fuente para personas: «evidencia EV-…», «check …», «documento …»."""
    return f"{KIND_LABELS.get(kind, kind)} {target}"
