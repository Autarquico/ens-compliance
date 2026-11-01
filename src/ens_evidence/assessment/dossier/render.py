"""Salida de la instantánea (JSON firmado, Markdown) y de la vista (secciones 10 y 11).

El JSON de la instantánea es lo que firman las transiciones; el Markdown y la vista son
derivados. Nada de lo que se escribe aquí dice «conforme»: la declaración la registra una
persona (regla 12).
"""

import hashlib
import json
from datetime import UTC, datetime

from pydantic import AwareDatetime, Field, JsonValue

from ens_evidence.assessment.dossier.methodology import methodology_text
from ens_evidence.assessment.dossier.model import DossierSnapshot, Section, SourceRef
from ens_evidence.matrix.messages import role_label
from ens_evidence.matrix.render import md_escape
from ens_evidence.models.contracts import SHA256_PATTERN, Strict
from ens_evidence.models.dossier import Transition
from ens_evidence.models.enums import DossierState, Role

NO_CORRECTIVE_MEASURES = "Sin medidas correctoras registradas."
DECLARED_LABEL = (
    "Declaración de conformidad registrada por {actor} ({role}); "
    "acto de la organización, la herramienta no declara"
)
EVIDENCE_REVIEW_NOTE = (
    "integridad del documento verificada; firma, formato PDF-A y vinculación los revisa una persona"
)
_METHODOLOGY = 6


def snapshot_sha256(snapshot: DossierSnapshot) -> str:
    return hashlib.sha256(render_snapshot_json(snapshot)).hexdigest()


def render_snapshot_json(snapshot: DossierSnapshot) -> bytes:
    document = snapshot.model_dump(mode="json")
    return (json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode()


def _instant(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


def _scalar(value: JsonValue) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "sí" if value else "no"
    return md_escape(str(value))


def _tree(value: JsonValue, depth: int) -> list[str]:
    pad = "  " * depth
    match value:
        case dict():
            lines: list[str] = []
            for key, item in value.items():
                head = f"{pad}- **{md_escape(str(key))}**:"
                if isinstance(item, dict | list) and item:
                    lines += [head, *_tree(item, depth + 1)]
                else:
                    lines.append(
                        f"{head} {'—' if isinstance(item, dict | list) else _scalar(item)}"
                    )
            return lines
        case list():
            lines = []
            for item in value:
                if isinstance(item, dict | list) and item:
                    lines += [f"{pad}-", *_tree(item, depth + 1)]
                else:
                    lines.append(
                        f"{pad}- {'—' if isinstance(item, dict | list) else _scalar(item)}"
                    )
            return lines
        case _:
            return [f"{pad}- {_scalar(value)}"]


def _source_line(source: SourceRef) -> str:
    moments = ", ".join(_instant(t) for t in source.timestamps)
    return (
        f"- {md_escape(source.kind)} · {md_escape(source.key)} · sha256 {source.sha256} · {moments}"
    )


def _methodology_lines() -> list[str]:
    return [f"> {md_escape(paragraph)}" for paragraph in methodology_text().split("\n\n")]


def _section_md(section: Section) -> list[str]:
    lines = [
        f"## {section.number}. {md_escape(section.title)}",
        "",
        f"Estado: {section.state.value}",
    ]
    lines += (
        ["", "Fuentes:", *(_source_line(s) for s in section.sources)] if section.sources else []
    )
    lines.append("")
    if section.number == _METHODOLOGY and isinstance(section.content, dict):
        meta = {k: v for k, v in section.content.items() if k != "text"}
        lines += [*_tree(meta, 0), "", *_methodology_lines()]
    else:
        lines += _tree(section.content, 0)
    return [*lines, ""]


def _header(snapshot: DossierSnapshot) -> list[str]:
    inputs = snapshot.inputs
    lines = [
        "# Expediente de autoevaluación ENS",
        "",
        f"> {md_escape(snapshot.commitment)}",
    ]
    if snapshot.notice:
        lines += [f"> {md_escape(snapshot.notice)}"]
    return [
        *lines,
        "",
        f"Categoría: {snapshot.category.value}",
        f"Generado: {_instant(inputs.generated_at)}",
        f"Evaluado: {_instant(inputs.evaluated_at)}",
        f"Referencia temporal: {_instant(inputs.reference_now)}",
        f"Producto: {md_escape(inputs.product_version)}",
        f"Metodología: {md_escape(inputs.methodology_version)}",
        "",
    ]


def render_snapshot_md(snapshot: DossierSnapshot) -> str:
    lines = _header(snapshot)
    for section in snapshot.sections:
        lines += _section_md(section)
    return "\n".join(lines).rstrip("\n") + "\n"


class TransitionEntry(Strict):
    """Un registro de la cadena tal como lo guarda el almacén."""

    key: str = Field(min_length=1)
    sha256: str = Field(pattern=SHA256_PATTERN)
    recorded_at: AwareDatetime
    record: Transition


class DossierView(Strict):
    """Lo que se añade a la instantánea al avanzar el expediente (secciones 10 y 11)."""

    snapshot: DossierSnapshot
    transitions: list[TransitionEntry]

    @property
    def analysis(self) -> TransitionEntry | None:
        return next(
            (e for e in self.transitions if e.record.to_state is DossierState.ANALYZED), None
        )


def _conclusions_md(view: DossierView) -> list[str]:
    lines = ["## 10. Conclusiones", ""]
    analysis = view.analysis
    if analysis is None:
        pending = view.snapshot.sections[9].content
        note = pending.get("note") if isinstance(pending, dict) else None
        return [*lines, _scalar(note), ""]
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
        f"elevación confirmada: {_scalar(record.elevation_confirmed)}.",
        "",
    ]


def _transition_md(entry: TransitionEntry) -> list[str]:
    record = entry.record
    who = f"{md_escape(record.actor)} ({role_label(record.role)})"
    lines = [
        f"### {record.to_state.value}",
        "",
        f"- Persona: {who}",
        f"- at: {_instant(record.at)}",
        f"- recorded_at: {_instant(entry.recorded_at)}",
        f"- Clave: {md_escape(entry.key)}",
        f"- sha256 del registro: {entry.sha256}",
    ]
    if record.to_state is DossierState.DECLARED:
        label = DECLARED_LABEL.format(actor=md_escape(record.actor), role=role_label(record.role))
        lines.insert(2, f"- {label}")
        lines.append(f"- sha256 del análisis citado: {record.analysis_sha256}")
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
    view = DossierView(snapshot=snapshot, transitions=transitions)
    lines = _header(snapshot)
    for section in snapshot.sections[:9]:
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
