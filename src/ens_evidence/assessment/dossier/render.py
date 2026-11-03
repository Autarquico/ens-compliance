"""Salida de la instantánea (JSON firmado, Markdown) y de la vista (secciones 10 y 11).

El JSON de la instantánea es lo que firman las transiciones; el Markdown y la vista son
derivados. Nada de lo que se escribe aquí dice «conforme»: la declaración la registra una
persona (regla 12).
"""

import hashlib
import json
from datetime import UTC, datetime

from ens_evidence.assessment.dossier.model import (
    SECTION_TITLES,
    DossierSnapshot,
    Section,
    SourceRef,
)
from ens_evidence.assessment.dossier.notices import NOT_READY_NOTICE
from ens_evidence.assessment.dossier.sections_md import (
    SECTION_STATE_LABELS,
    category_name,
    scalar,
    section_body,
)
from ens_evidence.assessment.dossier.view import (
    DossierView,
    TransitionEntry,
    snapshot_readiness,
)
from ens_evidence.matrix.messages import role_label
from ens_evidence.matrix.render import md_escape
from ens_evidence.models.enums import DossierState, Readiness, Role

NO_CORRECTIVE_MEASURES = "Sin medidas correctoras registradas."
DECLARED_LABEL = (
    "Declaración de conformidad registrada por {actor} ({role}); "
    "acto de la organización, la herramienta no declara"
)
EVIDENCE_REVIEW_NOTE = (
    "integridad del documento verificada; firma, formato PDF-A y vinculación los revisa una persona"
)
FIRST_HUMAN_SECTION = 10


def snapshot_sha256(snapshot: DossierSnapshot) -> str:
    return hashlib.sha256(render_snapshot_json(snapshot)).hexdigest()


def render_snapshot_json(snapshot: DossierSnapshot) -> bytes:
    document = snapshot.model_dump(mode="json")
    return (json.dumps(document, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode()


def _instant(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


def _source_line(source: SourceRef) -> str:
    moments = ", ".join(_instant(t) for t in source.timestamps)
    return (
        f"- {md_escape(source.kind)} · {md_escape(source.key)} · sha256 {source.sha256} · {moments}"
    )


def _section_md(section: Section) -> list[str]:
    lines = [
        f"## {section.number}. {md_escape(section.title)}",
        "",
        f"Estado de la sección: {SECTION_STATE_LABELS[section.state.value]}",
    ]
    lines += (
        ["", "Fuentes:", *(_source_line(s) for s in section.sources)] if section.sources else []
    )
    body = section_body(section)
    while body and body[-1] == "":
        body.pop()
    return [*lines, "", *body, ""]


def _not_ready_notice(snapshot: DossierSnapshot) -> list[str]:
    """Una línea en la cabecera si el sistema no estaba preparado al generar el expediente."""
    if snapshot_readiness(snapshot) is Readiness.READY:
        return []
    return [f"> {NOT_READY_NOTICE}"]


def _header(snapshot: DossierSnapshot) -> list[str]:
    inputs = snapshot.inputs
    lines = [
        "# Expediente de autoevaluación ENS",
        "",
        f"> {md_escape(snapshot.commitment)}",
    ]
    if snapshot.notice:
        lines.append(f"> {md_escape(snapshot.notice)}")
    return [
        *lines,
        *_not_ready_notice(snapshot),
        "",
        f"Categoría: {category_name(snapshot.category)}",
        f"Generado: {_instant(inputs.generated_at)}",
        f"Evaluado: {_instant(inputs.evaluated_at)}",
        f"Referencia temporal: {_instant(inputs.reference_now)}",
        f"Producto: {md_escape(inputs.product_version)}",
        f"Metodología: {md_escape(inputs.methodology_version)}",
        "",
    ]


def _canonical(snapshot: DossierSnapshot) -> DossierSnapshot:
    """La instantánea con las claves de los diccionarios en el orden del JSON firmado, que es el
    que se obtiene al releerla: el Markdown no depende de cómo se construyó en memoria."""
    return DossierSnapshot.model_validate_json(render_snapshot_json(snapshot))


def render_snapshot_md(snapshot: DossierSnapshot) -> str:
    snapshot = _canonical(snapshot)
    lines = _header(snapshot)
    for section in snapshot.sections:
        lines += _section_md(section)
    return "\n".join(lines).rstrip("\n") + "\n"


def _conclusions_md(view: DossierView) -> list[str]:
    lines = ["## 10. Conclusiones", ""]
    analysis = view.analysis
    if analysis is None:
        pending = view.snapshot.section(FIRST_HUMAN_SECTION).content
        note = pending.get("note") if isinstance(pending, dict) else None
        return [*lines, scalar(note), ""]
    record = analysis.record
    lines += [
        f"Análisis de {md_escape(record.actor)} ({role_label(record.role)}), "
        f"{_instant(record.at)}.",
        "",
        "Conclusiones:",
        *(f"> {md_escape(p)}" for p in (record.conclusions or "").split("\n\n")),
        "",
        "Medidas correctoras:",
    ]
    if record.corrective_measures:
        lines += [
            f"- {md_escape(m.description)} (dirigida a {role_label(m.addressee)})"
            for m in record.corrective_measures
        ]
    else:
        lines.append(f"- {NO_CORRECTIVE_MEASURES}")
    elevated_at = _instant(record.elevated_at) if record.elevated_at else "—"
    return [
        *lines,
        "",
        f"Elevado a {md_escape(record.elevated_to or '—')} "
        f"({role_label(Role.SYSTEM_OWNER)}) el {elevated_at}; "
        f"elevación confirmada: {scalar(record.elevation_confirmed)}.",
        "",
    ]


def _transition_md(entry: TransitionEntry) -> list[str]:
    record = entry.record
    who = f"{md_escape(record.actor)} ({role_label(record.role)})"
    lines = [
        f"### {record.to_state.value}",
        "",
        f"- Persona: {who}",
        f"- Cuándo se hizo: {_instant(record.at)}",
        f"- Cuándo se registró: {_instant(entry.recorded_at)}",
        f"- Clave: {md_escape(entry.key)}",
        f"- sha256 del registro: {entry.sha256}",
    ]
    if record.to_state is DossierState.DECLARED:
        label = DECLARED_LABEL.format(actor=md_escape(record.actor), role=role_label(record.role))
        lines.insert(2, f"- {label}")
        lines.append(f"- sha256 del análisis citado: {record.analysis_sha256}")
        if record.acknowledged_incomplete:
            sections = "; ".join(
                f"{n}. {SECTION_TITLES[n - 1]}" for n in record.acknowledged_incomplete
            )
            lines.append(f"- Declarada reconociendo secciones incompletas: {sections}")
        if record.declaration_evidence_id:
            lines.append(
                f"- Documento enlazado: {md_escape(record.declaration_evidence_id)}; "
                f"{EVIDENCE_REVIEW_NOTE}"
            )
    return [*lines, ""]


def _approval_md(view: DossierView) -> list[str]:
    lines = [
        "## 11. Aprobación/fecha",
        "",
        f"sha256 de la instantánea: {snapshot_sha256(view.snapshot)}",
    ]
    analysis = view.analysis
    lines.append(f"sha256 del análisis: {analysis.sha256 if analysis else '—'}")
    lines.append("")
    if not view.transitions:
        return [
            *lines,
            "Sin transiciones registradas: ninguna persona ha avanzado el expediente.",
            "",
        ]
    for entry in view.transitions:
        lines += _transition_md(entry)
    return lines


def render_view_md(snapshot: DossierSnapshot, transitions: list[TransitionEntry]) -> str:
    snapshot = _canonical(snapshot)
    view = DossierView(snapshot=snapshot, transitions=transitions)
    lines = _header(snapshot)
    for section in [s for s in snapshot.sections if s.number < FIRST_HUMAN_SECTION]:
        lines += _section_md(section)
    lines += _conclusions_md(view)
    lines += _approval_md(view)
    return "\n".join(lines).rstrip("\n") + "\n"


__all__ = [
    "DossierView",
    "TransitionEntry",
    "render_snapshot_json",
    "render_snapshot_md",
    "render_view_md",
    "snapshot_sha256",
]
