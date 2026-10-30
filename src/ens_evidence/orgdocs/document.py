"""Composición de un documento: secciones renderizadas más tabla de trazabilidad."""

import hashlib
import json

from ens_evidence.catalog import catalog_sha256, load_catalog
from ens_evidence.orgdocs.answers import NotApplicable, Value, condition_met
from ens_evidence.orgdocs.clauses import clause_index
from ens_evidence.orgdocs.meta import DocumentMeta, TemplateRef
from ens_evidence.orgdocs.render import RENDERER_VERSION, escape_markdown, render_document
from ens_evidence.orgdocs.sources import articulado_sha256, load_articulado
from ens_evidence.orgdocs.template import Line, LoadedTemplate, variables
from ens_evidence.orgdocs.trace import supported_clauses, supported_measures


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def used_questions(loaded: LoadedTemplate, values: dict[str, Value]) -> set[str]:
    """Preguntas que deciden el documento: las de cada `when` y las que pintan las secciones
    renderizadas. Una respuesta que el documento no muestra no invalida su aprobación."""
    used = {s.when.question for s in loaded.template.sections if s.when is not None}
    for section in loaded.template.sections:
        if section.when is None or condition_met(section.when, values):
            for node in loaded.bodies[section.id]:
                used.update(variables(node.text) if isinstance(node, Line) else [node.variable])
    return used


def answers_sha256(loaded: LoadedTemplate, values: dict[str, Value]) -> str:
    """Hash canónico de las respuestas que usa el documento (no de todo el fichero)."""
    used = {
        q.id: (
            {"not_applicable": True, "reason": values[q.id].reason}
            if isinstance(values[q.id], NotApplicable)
            else values[q.id]
        )
        for q in loaded.template.questions
        if q.id in used_questions(loaded, values)
    }
    return sha256_text(json.dumps(used, sort_keys=True, ensure_ascii=False, separators=(",", ":")))


def catalog_consolidation_date():
    return load_catalog().measures[0].source.consolidation_date


def _cell(text: str) -> str:
    return escape_markdown(" ".join(text.split()))


def _traceability(loaded: LoadedTemplate, values: dict[str, Value]) -> str:
    index = clause_index()
    titles = {s.id: s.title for s in loaded.template.sections}
    catalog_date = catalog_consolidation_date()
    rows = [
        "| Cláusula | Texto oficial | Sección |",
        "| --- | --- | --- |",
    ]
    for clause in supported_clauses(loaded, values):
        official = index[clause.id]
        cells = (official.reference, official.text, titles[clause.section])
        rows.append("| " + " | ".join(_cell(cell) for cell in cells) + " |")
    measures = ", ".join(m.id for m in supported_measures(loaded, values)) or "ninguna"
    return "\n".join(
        [
            "## Trazabilidad con el ENS",
            "",
            f"Medidas del anexo II a las que da soporte: {measures}. "
            "Que el documento cubra una cláusula no acredita que la medida esté implantada.",
            "",
            f"Plantilla `{loaded.id}` v{loaded.version}; renderizador v{RENDERER_VERSION}; "
            f"anexo II consolidado el {catalog_date} (catálogo sha256 {catalog_sha256()[:12]}); "
            f"articulado consolidado el {load_articulado().consolidation_date} "
            f"(sha256 {articulado_sha256()[:12]}).",
            "",
            *rows,
        ]
    )


def compose(loaded: LoadedTemplate, values: dict[str, Value]) -> tuple[str, DocumentMeta]:
    bodies = dict(render_document(loaded, values))
    parts = [f"# {loaded.template.title}"]
    for section in loaded.template.sections:
        if section.id not in bodies:
            continue
        parts += [f"## {section.title}", bodies[section.id]]
    parts.append(_traceability(loaded, values))
    text = "\n\n".join(part for part in parts if part) + "\n"
    meta = DocumentMeta(
        document=loaded.id,
        template=TemplateRef(id=loaded.id, version=loaded.version),
        renderer_version=RENDERER_VERSION,
        catalog_sha256=catalog_sha256(),
        catalog_consolidation_date=catalog_consolidation_date(),
        articulado_sha256=articulado_sha256(),
        articulado_consolidation_date=load_articulado().consolidation_date,
        answers_sha256=answers_sha256(loaded, values),
        document_sha256=sha256_text(text),
        clauses=supported_clauses(loaded, values),
        measures=supported_measures(loaded, values),
    )
    return text, meta
