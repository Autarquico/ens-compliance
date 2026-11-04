"""Markdown legible de las secciones 1 a 9, en castellano: lo derivado de la instantánea firmada.

El JSON es la fuente; aquí solo se presenta. Cada sección tiene su plantilla; si el contenido no
tiene la forma esperada (la instantánea guarda JSON sin tipar), se cae a un volcado genérico para no
perder nada. Nada de lo que se escribe dice «conforme»: la valoración es de la persona que revisa.
"""

import re
from collections.abc import Callable, Iterable
from datetime import UTC, datetime

from pydantic import JsonValue

from ens_evidence.assessment.dossier.model import Section
from ens_evidence.matrix import MatrixExport
from ens_evidence.matrix.messages import (
    ASSESSMENT_LABELS as MATRIX_ASSESSMENT_LABELS,
)
from ens_evidence.matrix.messages import (
    DECLARATION_LABELS,
    READINESS_LABELS,
    ROW_KIND_LABELS,
    role_label,
)
from ens_evidence.matrix.render import ExportedRow, md_escape
from ens_evidence.models.enums import Category, MeasureAssessment, Readiness, Role
from ens_evidence.models.readiness import Blocker, BlockerKind

CATEGORY_NAMES = {Category.BASICA: "BÁSICA", Category.MEDIA: "MEDIA", Category.ALTA: "ALTA"}
ASSESSMENT_LABELS = {
    **MATRIX_ASSESSMENT_LABELS,
    MeasureAssessment.COMPLIANT: "implantada según la revisión humana",
    MeasureAssessment.NON_COMPLIANT: "con carencias según la revisión humana",
}

SECTION_STATE_LABELS = {
    "RECORDED": "registrada",
    "INCOMPLETE": "incompleta",
    "PENDING_HUMAN_REVIEW": "pendiente de revisión humana",
}
STATE_LABELS = {
    "APPROVED": "aprobado",
    "CURRENT": "vigente",
    "STALE": "no vigente (algo cambió después de firmarlo)",
    "NOT_APPROVED": "sin aprobar",
    "INVALID": "no válido",
    "NOT_GENERATED": "sin generar",
    "GENERATED": "generado, sin aprobar",
    "OBSOLETE": "obsoleto",
    "OPEN": "abierto",
    "open": "abierto",
    "closed": "cerrado",
}
BLOCKER_SUMMARY = {
    BlockerKind.UNDETERMINED_APPLICABILITY: "aplicabilidad sin determinar",
    BlockerKind.NOT_APPLICABLE_WITHOUT_JUSTIFICATION: "no aplicable sin justificar",
    BlockerKind.MISSING_EVIDENCE: "falta evidencia",
    BlockerKind.EXPIRED_EVIDENCE: "evidencia caducada",
    BlockerKind.MISSING_REVIEW: "falta la revisión",
    BlockerKind.EXPIRED_REVIEW: "revisión caducada",
    BlockerKind.NOT_COMPLIANT: "valoración pendiente o desfavorable",
    BlockerKind.OPEN_FINDING: "hallazgo abierto",
}
AXIS_LABELS = {
    "implementation": "implantación",
    "applicability": "no aplica (revisión humana)",
}
NONE = "—"


def instant(value: datetime | str | None) -> str:
    if value is None:
        return NONE
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return md_escape(value)
    return value.astimezone(UTC).isoformat()


def esc(value: object) -> str:
    text = md_escape(str(value))
    return text or NONE


def scalar(value: JsonValue) -> str:
    if value is None:
        return NONE
    if isinstance(value, bool):
        return "sí" if value else "no"
    if isinstance(value, list) and all(not isinstance(v, dict | list) for v in value):
        return ", ".join(scalar(v) for v in value) or NONE
    return esc(value)


def category_name(category: Category | str) -> str:
    return CATEGORY_NAMES[Category(category)]


def person(name: object, role: object) -> str:
    try:
        role_text = role_label(Role(str(role)))
    except ValueError:
        role_text = esc(role)
    return f"{esc(name)} ({role_text})"


def state_label(value: object) -> str:
    return STATE_LABELS.get(str(value), esc(value))


def pending_summary(blockers: Iterable[Blocker]) -> str:
    """Los bloqueos de una medida por tipo y número; el detalle y el siguiente paso están en el
    JSON y en `matrix show`."""
    counts: dict[str, int] = {}
    for blocker in blockers:
        label = BLOCKER_SUMMARY[blocker.kind]
        counts[label] = counts.get(label, 0) + 1
    return "; ".join(f"{label} ({n})" if n > 1 else label for label, n in counts.items())


def table(headers: list[str], rows: Iterable[list[str]]) -> list[str]:
    body = [f"| {' | '.join(row)} |" for row in rows]
    if not body:
        return []
    return [f"| {' | '.join(headers)} |", f"|{'---|' * len(headers)}", *body, ""]


def _leaf(value: JsonValue) -> str:
    return NONE if isinstance(value, dict | list) else scalar(value)


def tree(value: JsonValue, depth: int = 0) -> list[str]:
    """Volcado genérico de un contenido sin plantilla (o que no encaja en ella)."""
    pad = "  " * depth
    match value:
        case dict():
            lines: list[str] = []
            for key, item in value.items():
                head = f"{pad}- **{md_escape(str(key))}**:"
                if isinstance(item, dict | list) and item:
                    lines += [head, *tree(item, depth + 1)]
                else:
                    lines.append(f"{head} {_leaf(item)}")
            return lines
        case list():
            lines = []
            for item in value:
                if isinstance(item, dict | list) and item:
                    lines += [f"{pad}-", *tree(item, depth + 1)]
                else:
                    lines.append(f"{pad}- {_leaf(item)}")
            return lines
        case _:
            return [f"{pad}- {scalar(value)}"]


def _approval_line(approval: dict | None, what: str) -> str:
    if not approval:
        return f"- {what}: sin aprobar."
    return (
        f"- {what}: aprobado por {person(approval['approved_by'], approval['role'])} "
        f"el {instant(approval['approved_at'])}."
    )


# 1 ----------------------------------------------------------------------------------------------


def identification(content: dict) -> list[str]:
    system = content["system"]
    catalog = content["catalog_version"]
    return [
        f"- Sistema: {esc(system['name'])}",
        f"- Descripción: {esc(system['description'])}",
        f"- Categoría: {category_name(content['category'])}",
        f"- {esc(content['notice'])}",
        f"- Catálogo de medidas: consolidado el {esc(catalog['consolidation_date'])} "
        f"(huella {esc(catalog['snapshot_sha256'])})",
        f"- Versión del producto: {esc(content['product_version'])}",
        f"- Versión de la metodología: {esc(content['methodology_version'])}",
    ]


# 2 ----------------------------------------------------------------------------------------------


def _inventory_lines(title: str, items: list[dict]) -> list[str]:
    lines = [f"{title} ({len(items)}):", ""]
    for item in items:
        kind = f" ({esc(item['type'])})" if item.get("type") else ""
        facts = "; ".join(
            f"{esc(k)}: {scalar(v)}"
            for k, v in item.items()
            if k not in ("id", "type", "name") and v not in (None, [], {})
        )
        name = f" {esc(item['name'])}" if item.get("name") else ""
        lines.append(f"- **{esc(item['id'])}**{name}{kind}" + (f": {facts}" if facts else ""))
    return [*lines, ""]


def scope(content: dict) -> list[str]:
    inventory = content["inventory"]
    system = inventory["system"]
    lines = [
        f"Estado: {state_label(content['state'])}.",
        _approval_line(content["approval"], "Alcance"),
        f"- Huella del alcance: {esc(content['fingerprint'])}",
        "",
        f"Activos incluidos: {', '.join(esc(a) for a in system['included_assets']) or NONE}.",
        f"Servicios incluidos: {', '.join(esc(s) for s in system['included_services']) or NONE}.",
        "",
    ]
    if system["exclusions"]:
        lines += ["Exclusiones:", ""]
        lines += [
            f"- {esc(x['kind'])} {esc(x['ref'])}: {esc(x['justification'])}"
            for x in system["exclusions"]
        ]
        lines.append("")
    lines += _inventory_lines("Activos", inventory["assets"]["assets"])
    lines += _inventory_lines("Servicios", inventory["services"]["services"])
    dependencies = inventory["dependencies"]
    if dependencies:
        lines += _inventory_lines("Dependencias", next(iter(dependencies.values())))
    return lines


# 3 ----------------------------------------------------------------------------------------------

DIMENSIONS = {
    "C": "confidencialidad",
    "I": "integridad",
    "T": "trazabilidad",
    "A": "autenticidad",
    "D": "disponibilidad",
}


def categorization(content: dict) -> list[str]:
    lines = [
        f"Estado: {state_label(content['state'])}.",
        f"Categoría resultante: {category_name(content['category'])}.",
        _approval_line(content["approval"], "Categorización"),
        "",
    ]
    rows = [
        [
            f"{esc(level['dimension'])} ({DIMENSIONS.get(level['dimension'], '')})",
            esc(level["level"]),
            ", ".join(esc(x) for x in level["determined_by"]) or NONE,
        ]
        for level in content["levels"]
    ]
    return [*lines, *table(["Dimensión", "Nivel", "Determinado por"], rows)]


# 4 ----------------------------------------------------------------------------------------------


def risks(content: dict) -> list[str]:
    lines = [
        f"Estado: {state_label(content['state'])}.",
        _approval_line(content["approval"], "Análisis de riesgos"),
        "",
    ]
    problems = [*content["state_errors"], *content["validation_errors"]]
    if problems:
        lines += ["Pendiente:", "", *(f"- {esc(p)}" for p in problems), ""]
    methodology = content["methodology"]
    if methodology:
        lines += [
            f"Metodología: {esc(methodology['name'])}; "
            f"referencia: {esc(methodology['reference'])}.",
            "",
        ]
    register = content["register"]
    if register:
        rows = [
            [
                esc(r["id"]),
                esc(r["threat"]),
                ", ".join(esc(a) for a in [*r["assets"], *r["services"]]) or NONE,
                esc(r["inherent"]),
                esc(r["residual"]),
                esc(r["treatment"]),
                esc(r["owner"]),
            ]
            for r in register["risks"]
        ]
        lines += table(
            ["Riesgo", "Amenaza", "Afecta a", "Inherente", "Residual", "Tratamiento", "Dueño"],
            rows,
        )
    return lines


# 5 ----------------------------------------------------------------------------------------------


def _required_by(value: object) -> str:
    if value is None:
        return "elegida por la organización"
    return esc(re.sub(r"\bBASICA\b", CATEGORY_NAMES[Category.BASICA], str(value)))


def applicability(content: dict) -> list[str]:
    declaration = content["declaration"]
    lines = [
        f"Estado: {state_label(content['state'])}.",
        _approval_line(declaration, "Declaración de Aplicabilidad"),
    ]
    lines += [f"- {esc(r)}" for r in content["reasons"]]
    rows = [
        [
            esc(m["id"]),
            _required_by(m["required_by"]),
            ", ".join(esc(x) for x in m["chosen_reinforcements"]) or NONE,
        ]
        for m in declaration["selected"]
    ]
    return [
        *lines,
        "",
        f"Medidas seleccionadas: {len(rows)}.",
        "",
        *table(["Medida", "Exigida por", "Refuerzos elegidos"], rows),
    ]


# 6 ----------------------------------------------------------------------------------------------


def methodology(content: dict) -> list[str]:
    paragraphs = str(content["text"]).split("\n\n")
    return [
        f"Versión {esc(content['version'])} · huella {esc(content['sha256'])}",
        "",
        *(f"> {esc(p)}" for p in paragraphs),
    ]


# 7 ----------------------------------------------------------------------------------------------


def _last_review(row: ExportedRow) -> str:
    review = row.last_review
    if review is None:
        return "sin revisión"
    assessment = (
        ASSESSMENT_LABELS[review.assessment] if review.assessment else AXIS_LABELS[review.axis]
    )
    return (
        f"{esc(assessment)} · {esc(review.reviewer)}, {instant(review.reviewed_at)} · "
        f"válida hasta {instant(review.valid_until)}"
    )


def _periodicity(row: ExportedRow) -> str:
    cadence = f"cada {row.review_every_days} días" if row.review_every_days else "sin periodicidad"
    current = "vigente" if row.review_current else "no vigente"
    return f"{cadence}; {current}"


def _evidence_ids(row: ExportedRow) -> str:
    ids = [link.target for link in [*row.technical, *row.manual]]
    return ", ".join(esc(i) for i in ids) or NONE


def _row_cells(row: ExportedRow) -> list[str]:
    pending = esc(pending_summary(row.blockers)) if row.blockers else NONE
    return [
        esc(row.measure_id),
        esc(row.name),
        esc(ROW_KIND_LABELS[row.kind]),
        "sí" if row.implemented else "no",
        _periodicity(row),
        _last_review(row),
        _evidence_ids(row),
        pending,
    ]


def results(content: dict) -> list[str]:
    export = MatrixExport.model_validate(content["matrix"])
    declaration = export.declaration
    ready = export.readiness is Readiness.READY and not export.errors
    not_ready = " (sirve para la autoevaluación y el análisis, no para declarar)"
    lines = [
        f"Preparación según la regla del producto: {READINESS_LABELS[export.readiness]}"
        f"{'' if ready else not_ready}.",
        f"Declaración de Aplicabilidad: {DECLARATION_LABELS[declaration.state]}.",
        f"Medidas: {len(export.rows)}; bloqueos abiertos: {len(export.blockers)}.",
        "«Implantada» recoge la valoración de la persona que revisó la medida; "
        "la herramienta no la decide.",
        "",
    ]
    lines += [f"Error al leer: {esc(e)}" for e in export.errors]
    headers = [
        "Medida",
        "Nombre",
        "Tipo",
        "Implantada",
        "Revisión regular",
        "Última revisión",
        "Evidencias",
        "Pendiente",
    ]
    return [*lines, *table(headers, (_row_cells(r) for r in export.rows))]


# 8 ----------------------------------------------------------------------------------------------


def evidence(content: dict) -> list[str]:
    documents = content["documents"]
    lines: list[str] = []
    manifests = [
        [
            esc(m["id"]),
            esc(m["source"]),
            instant(m["collected_at"]),
            instant(m["valid_until"]),
            esc(m["artifact_sha256"]),
        ]
        for m in content["manifests"]
    ]
    lines += ["Evidencias manuales y recogidas:", ""]
    lines += table(["Id", "Fuente", "Recogida", "Válida hasta", "Huella del artefacto"], manifests)
    lines += [
        f"Evidencias de entrada de checks (se renuevan en cada recogida): "
        f"{len(content['check_inputs'])}.",
        f"Conectores de checks: {', '.join(esc(c) for c in content['check_connectors']) or NONE}.",
        f"Revisiones humanas citadas: {len(content['reviews'])}.",
        "",
        "Documentos de la organización:",
        "",
    ]
    statuses = [[esc(s["name"]), state_label(s["state"])] for s in documents["statuses"]]
    lines += table(["Documento", "Estado"], statuses)
    coverage = [
        [
            esc(m["measure_id"]),
            esc(m["document"]),
            ", ".join(esc(c) for c in m["covered_clauses"]) or NONE,
            ", ".join(esc(c) for c in m["missing_clauses"]) or NONE,
        ]
        for m in documents["measures"]
    ]
    lines += table(["Medida", "Documento", "Cláusulas cubiertas", "Cláusulas sin cubrir"], coverage)
    incidents = [
        [
            esc(i["id"]),
            "simulacro" if i["simulated"] else "incidente",
            state_label(i["status"]),
            instant(i["detected_at"]),
            str(i["records"]),
            "; ".join(esc(p) for p in i["problems"]) or NONE,
        ]
        for i in content["incidents"]
    ]
    lines += ["Registro de incidentes:", ""]
    lines += table(["Id", "Tipo", "Estado", "Detectado", "Registros", "Problemas"], incidents)
    if not incidents:
        lines += ["Sin incidentes registrados.", ""]
    return lines


# 9 ----------------------------------------------------------------------------------------------


def _sentence(text: str) -> str:
    """La primera letra en mayúscula sin tocar el resto (ids, instantes, clases)."""
    return text[:1].upper() + text[1:]


def treatment(content: dict) -> list[str]:
    readiness = Readiness(content["readiness"])
    lines = [
        f"{_sentence(esc(content['pre_audit']))}.",
        f"Preparación: {READINESS_LABELS[readiness]}.",
        "",
    ]
    by_measure: dict[str, list] = {}
    for raw in content["open_blockers"]:
        blocker = Blocker.model_validate(raw)
        by_measure.setdefault(blocker.measure_id or "—", []).append(blocker)
    if by_measure:
        lines += [
            "Incumplimientos abiertos por medida (el detalle y el siguiente paso, con "
            "`assessment matrix show`):",
            "",
        ]
        for measure, found in by_measure.items():
            lines.append(f"- **{esc(measure)}**: {esc(pending_summary(found))}")
        lines.append("")
    else:
        lines += ["Sin incumplimientos abiertos.", ""]
    plan = content["treatment_plan"]
    if plan:
        rows = [
            [
                esc(a["id"]),
                esc(a["risk"]),
                esc(a["description"]),
                esc(a["owner"]),
                esc(a["due"]),
                esc(a["status"]),
            ]
            for a in plan["actions"]
        ]
        lines += ["Plan de tratamiento:", ""]
        lines += table(["Acción", "Riesgo", "Descripción", "Responsable", "Plazo", "Estado"], rows)
    else:
        lines += ["No hay plan de tratamiento registrado.", ""]
    lines += [f"- {esc(e)}" for e in content["treatment_plan_errors"]]
    return lines


def pending_note(content: dict) -> list[str]:
    return [esc(content["note"])]


RENDERERS: dict[int, Callable[[dict], list[str]]] = {
    1: identification,
    2: scope,
    3: categorization,
    4: risks,
    5: applicability,
    6: methodology,
    7: results,
    8: evidence,
    9: treatment,
    10: pending_note,
    11: pending_note,
}


def section_body(section: Section) -> list[str]:
    """La plantilla de la sección; el volcado genérico solo si el contenido no es un objeto.

    Si el contenido generado por la herramienta no encaja en su plantilla es un fallo de la
    plantilla: se propaga en lugar de degradar el Markdown en silencio."""
    renderer = RENDERERS.get(section.number)
    if renderer is not None and isinstance(section.content, dict):
        return renderer(section.content)
    return tree(section.content)


__all__ = ["category_name", "esc", "instant", "person", "scalar", "section_body", "state_label"]
