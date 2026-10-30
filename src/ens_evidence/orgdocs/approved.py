"""Trazabilidad con vigencia: qué documento aprobado y vigente cubre cada cláusula y medida.

Solo cuentan los documentos con aprobación vigente (`DocumentState.APPROVED`). Es cobertura
documental: no implica que la medida esté implantada ni cambia su evaluación.
"""

from dataclasses import dataclass, field

from ens_evidence.orgdocs.answers import read_answers, validate_answers
from ens_evidence.orgdocs.approval import DocumentApproval, all_statuses
from ens_evidence.orgdocs.meta import SupportedMeasure
from ens_evidence.orgdocs.template import load_templates
from ens_evidence.orgdocs.trace import Coverage, supported_clauses, supported_measures
from ens_evidence.workspace import WorkspaceStore


@dataclass(frozen=True)
class ApprovedCoverage:
    coverage: Coverage
    approval: DocumentApproval


@dataclass
class ApprovedTrace:
    clauses: dict[str, list[ApprovedCoverage]] = field(default_factory=dict)
    measures: dict[str, dict[str, SupportedMeasure]] = field(default_factory=dict)


def approved_coverage(store: WorkspaceStore) -> ApprovedTrace:
    """Cláusula -> secciones aprobadas que la cubren; medida -> documento aprobado -> cláusulas
    cubiertas y no cubiertas por ese documento."""
    current = {s.name: s.approval for s in all_statuses(store) if s.current and s.approval}
    trace = ApprovedTrace()
    values = validate_answers(read_answers(store), load_templates()).values if current else {}
    for loaded in load_templates():
        approval = current.get(loaded.id)
        if approval is None:
            continue
        titles = {s.id: s.title for s in loaded.template.sections}
        for clause in supported_clauses(loaded, values):
            coverage = Coverage(loaded.id, clause.section, titles[clause.section])
            trace.clauses.setdefault(clause.id, []).append(ApprovedCoverage(coverage, approval))
        for measure in supported_measures(loaded, values):
            trace.measures.setdefault(measure.id, {})[loaded.id] = measure
    return trace
