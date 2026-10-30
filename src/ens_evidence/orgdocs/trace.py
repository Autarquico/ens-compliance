"""Trazabilidad: qué documento y sección cubre cada cláusula y medida del anexo II."""

from dataclasses import dataclass

from ens_evidence.orgdocs.answers import Value, condition_met
from ens_evidence.orgdocs.clauses import clause_index, measure_clause_ids
from ens_evidence.orgdocs.meta import SupportedClause, SupportedMeasure
from ens_evidence.orgdocs.template import LoadedTemplate, Section, load_templates


@dataclass(frozen=True)
class Coverage:
    document: str
    section: str
    section_title: str


def rendered_sections(
    loaded: LoadedTemplate, values: dict[str, Value] | None = None
) -> list[Section]:
    """Secciones que el documento contiene con estas respuestas; todas si no se dan."""
    return [
        section
        for section in loaded.template.sections
        if values is None or section.when is None or condition_met(section.when, values)
    ]


def supported_clauses(
    loaded: LoadedTemplate, values: dict[str, Value] | None = None
) -> list[SupportedClause]:
    index = clause_index()
    return [
        SupportedClause(id=clause_id, origin=index[clause_id].origin, section=section.id)
        for section in rendered_sections(loaded, values)
        for clause_id in section.covers
    ]


def supported_measures(
    loaded: LoadedTemplate, values: dict[str, Value] | None = None
) -> list[SupportedMeasure]:
    index = clause_index()
    covered_by_measure: dict[str, set[str]] = {}
    for clause in supported_clauses(loaded, values):
        measure_id = index[clause.id].measure_id
        if measure_id is not None:
            covered_by_measure.setdefault(measure_id, set()).add(clause.id)
    return [
        SupportedMeasure(
            id=measure_id,
            covered_clauses=sorted(covered),
            missing_clauses=[c for c in measure_clause_ids(measure_id) if c not in covered],
        )
        for measure_id, covered in sorted(covered_by_measure.items())
    ]


def clause_coverage() -> dict[str, list[Coverage]]:
    """Cláusula (anexo II o articulado) -> secciones de las plantillas que la cubren, con
    independencia de las respuestas (lo que las plantillas pueden cubrir)."""
    coverage: dict[str, list[Coverage]] = {}
    for loaded in load_templates():
        titles = {s.id: s.title for s in loaded.template.sections}
        for clause in supported_clauses(loaded):
            coverage.setdefault(clause.id, []).append(
                Coverage(loaded.id, clause.section, titles[clause.section])
            )
    return coverage


def measure_coverage() -> dict[str, dict[str, SupportedMeasure]]:
    """Medida -> documento -> cláusulas cubiertas y no cubiertas por ese documento."""
    result: dict[str, dict[str, SupportedMeasure]] = {}
    for loaded in load_templates():
        for measure in supported_measures(loaded):
            result.setdefault(measure.id, {})[loaded.id] = measure
    return result
