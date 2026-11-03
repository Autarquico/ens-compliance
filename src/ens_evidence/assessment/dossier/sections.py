"""Las 11 secciones de la instantánea, cada una con las fuentes reales de las que sale.

Solo lee y compone: reutiliza las APIs de cada área sin reinterpretarlas. Ninguna sección dice
«conforme» (regla 12): lo que no ha hecho una persona queda pendiente. Una fuente es un fichero del
workspace con su clave y la huella de sus bytes; si el fichero no trae un instante propio (una
aprobación, una revisión, una recogida), el instante es el de su lectura (`evaluated_at`).
"""

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from ens_evidence.applicability import DECLARATION_KEY, DeclarationState, DeclarationStatus
from ens_evidence.approvals import load_model
from ens_evidence.assessment.dossier.methodology import (
    METHODOLOGY_VERSION,
    methodology_sha256,
    methodology_text,
)
from ens_evidence.assessment.dossier.model import (
    SECTION_TITLES,
    Section,
    SectionState,
    SnapshotInputs,
    SourceRef,
)
from ens_evidence.categorization import (
    APPROVAL_KEY as CATEGORIZATION_APPROVAL_KEY,
)
from ens_evidence.categorization import (
    CATEGORIZATION_KEY,
    CategorizationState,
    categorization_status,
    commitment,
    commitment_notice,
)
from ens_evidence.checks.store import scan_runs
from ens_evidence.incidents import check_all_incidents
from ens_evidence.matrix import MATRIX_KEY, MatrixExport
from ens_evidence.models.enums import Category, Readiness
from ens_evidence.orgdocs import all_statuses, approved_coverage
from ens_evidence.orgdocs.keys import ANSWERS_KEY, document_key, meta_key
from ens_evidence.risk import RiskState, TreatmentPlanFile, risk_status, validate_risk
from ens_evidence.risk.models import APPROVAL_KEY as RISK_APPROVAL_KEY
from ens_evidence.risk.models import (
    METHODOLOGY_KEY,
    METHODOLOGY_MD_KEY,
    REGISTER_KEY,
    TREATMENT_PLAN_KEY,
)
from ens_evidence.scope import load_inventory, scope_status
from ens_evidence.scope.approval import APPROVAL_KEY as SCOPE_APPROVAL_KEY
from ens_evidence.scope.inventory import ASSETS_KEY, DEPENDENCIES_KEY, SERVICES_KEY, SYSTEM_KEY
from ens_evidence.scope.sync import MEASURES_KEY
from ens_evidence.workspace import WorkspaceStore

CHECK_RUN = "check-run"
CHECK_EVIDENCE = "check-evidence"
INCIDENT_RECORD = "incident-record"
PRE_AUDIT_PENDING = "pre-auditoría no ejecutada; revisión humana pendiente"
CONCLUSIONS_PENDING = (
    "Conclusiones pendientes: las registra el responsable de la seguridad al analizar el informe "
    "(anexo III, 2.1.b) y las eleva al responsable del sistema."
)
APPROVAL_PENDING = (
    "Sin transiciones humanas registradas: autoevaluación, análisis y declaración son actos de "
    "personas de la organización."
)
SCOPE_KEYS = (ASSETS_KEY, SERVICES_KEY, DEPENDENCIES_KEY, SYSTEM_KEY)


@dataclass(frozen=True)
class SectionContext:
    store: WorkspaceStore
    inputs: SnapshotInputs
    category: Category
    declaration: DeclarationStatus
    export: MatrixExport

    @property
    def read_at(self) -> datetime:
        return self.inputs.evaluated_at

    def sources(self, kind: str, keys: Iterable[str], *stamps: datetime | None) -> list[SourceRef]:
        """Las fuentes que existen, con la huella de sus bytes y su instante (o el de lectura)."""
        moments = [s for s in stamps if s is not None] or [self.read_at]
        return [
            SourceRef(kind=kind, key=key, sha256=_digest(self.store, key), timestamps=moments)
            for key in keys
            if self.store.exists(key)
        ]


def _digest(store: WorkspaceStore, key: str) -> str:
    return hashlib.sha256(store.read_bytes(key)).hexdigest()


def _section(number: int, state: SectionState, sources: list[SourceRef], content: dict) -> Section:
    return Section(
        number=number,
        title=SECTION_TITLES[number - 1],
        state=state,
        sources=sources,
        content=content,
    )


def _dump(model) -> dict | None:
    return None if model is None else model.model_dump(mode="json")


# 1 ----------------------------------------------------------------------------------------------


def identification(ctx: SectionContext) -> Section:
    inventory = load_inventory(ctx.store).inventory
    assert inventory is not None  # categorización vigente implica inventario válido
    approved = scope_status(ctx.store).approval
    content = {
        "system": {
            "name": inventory.system.name,
            "description": inventory.system.description,
        },
        "category": ctx.category.value,
        "commitment": commitment(ctx.category),
        "notice": commitment_notice(ctx.category),
        "catalog_version": ctx.inputs.catalog_version.model_dump(mode="json"),
        "product_version": ctx.inputs.product_version,
        "methodology_version": ctx.inputs.methodology_version,
    }
    sources = ctx.sources("scope", [SYSTEM_KEY], approved and approved.approved_at)
    return _section(1, SectionState.RECORDED, sources, content)


# 2 ----------------------------------------------------------------------------------------------


def scope(ctx: SectionContext) -> Section:
    status = scope_status(ctx.store)
    inventory = load_inventory(ctx.store).inventory
    assert inventory is not None
    approved_at = status.approval and status.approval.approved_at
    content = {
        "state": status.state.value,
        "fingerprint": status.fingerprint,
        "approval": _dump(status.approval),
        "inventory": {
            "assets": _dump(inventory.assets),
            "services": _dump(inventory.services),
            "dependencies": _dump(inventory.dependencies),
            "system": _dump(inventory.system),
        },
    }
    sources = ctx.sources("scope", [*SCOPE_KEYS, SCOPE_APPROVAL_KEY], approved_at)
    return _section(2, SectionState.RECORDED, sources, content)


# 3 ----------------------------------------------------------------------------------------------


def categorization(ctx: SectionContext) -> Section:
    status = categorization_status(ctx.store, ctx.read_at)
    assert status.computation is not None
    current = status.state is CategorizationState.CURRENT
    approved_at = status.approval and status.approval.approved_at
    content = {
        "state": status.state.value,
        "category": ctx.category.value,
        "commitment": commitment(ctx.category),
        "levels": [
            {
                "dimension": level.dimension.value,
                "level": level.level.value if level.level else None,
                "determined_by": list(level.determined_by),
            }
            for level in status.computation.levels
        ],
        "approval": _dump(status.approval),
    }
    sources = ctx.sources(
        "categorization", [CATEGORIZATION_KEY, CATEGORIZATION_APPROVAL_KEY], approved_at
    )
    state = SectionState.RECORDED if current else SectionState.INCOMPLETE
    return _section(3, state, sources, content)


# 4 ----------------------------------------------------------------------------------------------


def risks(ctx: SectionContext) -> Section:
    status = risk_status(ctx.store, ctx.read_at)
    analysis, errors = validate_risk(ctx.store, ctx.read_at)
    content = {
        "state": status.state.value,
        "state_errors": list(status.errors),
        "approval": _dump(status.approval),
        "validation_errors": errors,
        "methodology": _dump(analysis and analysis.methodology),
        "register": _dump(analysis and analysis.register),
    }
    approved_at = status.approval and status.approval.approved_at
    sources = ctx.sources(
        "risk",
        [METHODOLOGY_MD_KEY, METHODOLOGY_KEY, REGISTER_KEY, RISK_APPROVAL_KEY],
        approved_at,
    )
    recorded = analysis is not None and status.state is RiskState.APPROVED
    return _section(
        4, SectionState.RECORDED if recorded else SectionState.INCOMPLETE, sources, content
    )


# 5 ----------------------------------------------------------------------------------------------


def applicability_declaration(ctx: SectionContext) -> Section:
    status = ctx.declaration
    declaration = status.declaration
    assert declaration is not None
    content = {
        "state": status.state.value,
        "reasons": list(status.errors),
        "declaration": _dump(declaration),
    }
    sources = ctx.sources("declaration", [DECLARATION_KEY, MEASURES_KEY], declaration.approved_at)
    current = status.state is DeclarationState.CURRENT
    return _section(
        5, SectionState.RECORDED if current else SectionState.INCOMPLETE, sources, content
    )


# 6 ----------------------------------------------------------------------------------------------


def methodology(ctx: SectionContext) -> Section:
    content = {
        "version": METHODOLOGY_VERSION,
        "sha256": methodology_sha256(),
        "text": methodology_text(),
    }
    return _section(6, SectionState.RECORDED, [], content)


# 7 ----------------------------------------------------------------------------------------------


def results(ctx: SectionContext) -> Section:
    export = ctx.export
    sources = ctx.sources("matrix", [MATRIX_KEY])
    ready = export.readiness is Readiness.READY and not export.errors
    return _section(
        7,
        SectionState.RECORDED if ready else SectionState.INCOMPLETE,
        sources,
        {"matrix": export.model_dump(mode="json")},
    )


# 8 ----------------------------------------------------------------------------------------------


def check_input_ids(export: MatrixExport) -> set[str]:
    """Evidencias que solo existen como entrada de un check: las renueva cada recogida."""
    manual = {link.target for row in export.rows for link in row.manual if link.kind == "evidence"}
    manual |= {
        reviewed.evidence_id
        for row in export.rows
        if row.last_review is not None
        for obligation in row.last_review.obligations
        for reviewed in obligation.evidence
    }
    return {i for check in export.sources.checks for i in check.evidence_ids} - manual


def evidence(ctx: SectionContext) -> Section:
    export = ctx.export
    inputs = check_input_ids(export)
    kept = [m for m in export.sources.evidence if m.id not in inputs]
    derived = [m for m in export.sources.evidence if m.id in inputs]
    sources = [
        *_manifest_sources(ctx, kept, "evidence-manifest"),
        *_manifest_sources(ctx, derived, CHECK_EVIDENCE),
        *_review_sources(ctx),
        *_document_sources(ctx),
        *_run_sources(ctx),
    ]
    incidents, incident_sources, problems = _incidents(ctx)
    content = {
        "manifests": [m.model_dump(mode="json") for m in kept],
        "check_inputs": [m.model_dump(mode="json") for m in derived],
        "reviews": [{"measure_id": r.measure_id, "key": r.key} for r in export.sources.reviews],
        "documents": _documents(ctx),
        "check_connectors": sorted(
            {c.connector for c in export.sources.checks if c.connector is not None}
        ),
        "incidents": incidents,
    }
    state = SectionState.INCOMPLETE if problems or export.errors else SectionState.RECORDED
    return _section(8, state, [*sources, *incident_sources], content)


def _manifest_sources(ctx: SectionContext, manifests: list, kind: str) -> list[SourceRef]:
    sources: list[SourceRef] = []
    for manifest in manifests:
        sources += ctx.sources(kind, [manifest.manifest_key], manifest.collected_at)
    return sources


def _review_sources(ctx: SectionContext) -> list[SourceRef]:
    sources: list[SourceRef] = []
    for row in ctx.export.rows:
        if row.last_review is not None:
            sources += ctx.sources("review", [row.last_review.key], row.last_review.recorded_at)
    return sources


def _documents(ctx: SectionContext) -> dict:
    coverage = approved_coverage(ctx.store)
    return {
        "statuses": [
            {
                "name": s.name,
                "state": s.state.value,
                "reasons": list(s.reasons),
                "unreadable": list(s.unreadable),
                "approval": _dump(s.approval),
            }
            for s in all_statuses(ctx.store)
        ],
        "measures": [
            {
                "measure_id": measure_id,
                "document": document,
                "covered_clauses": list(supported.covered_clauses),
                "missing_clauses": list(supported.missing_clauses),
            }
            for measure_id, documents in sorted(coverage.measures.items())
            for document, supported in sorted(documents.items())
        ],
    }


def _document_sources(ctx: SectionContext) -> list[SourceRef]:
    sources = ctx.sources("org-answers", [ANSWERS_KEY])
    for status in all_statuses(ctx.store):
        approved_at = status.approval and status.approval.approved_at
        keys = [document_key(status.name), meta_key(status.name)]
        sources += ctx.sources("org-document", keys, approved_at)
    return sources


def _run_sources(ctx: SectionContext) -> list[SourceRef]:
    """Las ejecuciones vivas, hasta el instante de lectura, de los conectores que sustentan la
    matriz: las posteriores no estaban cuando se generó el expediente."""
    scan = scan_runs(ctx.store)
    connectors = {c.connector for c in ctx.export.sources.checks if c.connector is not None}
    sources: list[SourceRef] = []
    for connector in sorted(connectors):
        for stored in scan.live(connector):
            if stored.run.finished_at > ctx.read_at:
                continue
            sources += ctx.sources(CHECK_RUN, [stored.key], stored.run.finished_at)
    return sources


def _incidents(ctx: SectionContext) -> tuple[list[dict], list[SourceRef], list[str]]:
    """Estado de cada incidente sin su texto libre; los problemas de verificación se registran."""
    content: list[dict] = []
    sources: list[SourceRef] = []
    problems: list[str] = []
    for incident_id, check in check_all_incidents(ctx.store).items():
        records = check.scan.records
        last = records[-1].record if records else None
        content.append(
            {
                "id": incident_id,
                "simulated": incident_id.startswith("SIM-"),
                "status": last.state.status.value if last else None,
                "detected_at": last.state.detected_at.isoformat() if last else None,
                "records": len(records),
                "problems": list(check.problems),
            }
        )
        problems += check.problems
        for stored in records:
            sources += ctx.sources(INCIDENT_RECORD, [stored.key], stored.record.recorded_at)
    return content, sources, problems


# 9 ----------------------------------------------------------------------------------------------


def treatment(ctx: SectionContext) -> Section:
    errors: list[str] = []
    plan = load_model(ctx.store, TREATMENT_PLAN_KEY, TreatmentPlanFile, errors)
    export = ctx.export
    content = {
        "pre_audit": PRE_AUDIT_PENDING,
        "readiness": export.readiness.value,
        "open_blockers": [b.model_dump(mode="json") for b in export.blockers],
        "treatment_plan": _dump(plan),
        "treatment_plan_errors": errors,
    }
    sources = ctx.sources("treatment-plan", [TREATMENT_PLAN_KEY])
    return _section(9, SectionState.PENDING_HUMAN_REVIEW, sources, content)


# 10 y 11 ----------------------------------------------------------------------------------------


def conclusions(ctx: SectionContext) -> Section:
    return _section(
        10,
        SectionState.PENDING_HUMAN_REVIEW,
        [],
        {"conclusions": None, "note": CONCLUSIONS_PENDING},
    )


def approval(ctx: SectionContext) -> Section:
    content = {"self_assessed": None, "analyzed": None, "declared": None, "note": APPROVAL_PENDING}
    return _section(11, SectionState.PENDING_HUMAN_REVIEW, [], content)


def build_sections(ctx: SectionContext) -> list[Section]:
    return [
        identification(ctx),
        scope(ctx),
        categorization(ctx),
        risks(ctx),
        applicability_declaration(ctx),
        methodology(ctx),
        results(ctx),
        evidence(ctx),
        treatment(ctx),
        conclusions(ctx),
        approval(ctx),
    ]
