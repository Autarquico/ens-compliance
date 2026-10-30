"""Vista legible de la Declaración de Aplicabilidad (art. 28.2 y 28.3 del RD 311/2022).

Solo presenta lo que la Declaración firmada y la matriz ya dicen; no decide nada. Con una
evaluación, anota cada medida con su estado en la matriz. Nunca afirma conformidad: una medida
figura como «implantada según la revisión de X el …» (regla 12).
"""

from datetime import datetime

from ens_evidence.applicability import Declaration, DeclarationState, declaration_status
from ens_evidence.applicability.declaration import (
    DECLARATION_KEY,
    AbsentAssetsRecord,
    CompensationRecord,
    NotSelected,
    SelectedMeasure,
)
from ens_evidence.catalog import load_catalog
from ens_evidence.matrix.evaluate import AssessmentResult
from ens_evidence.matrix.messages import DECLARATION_LABELS, role_label
from ens_evidence.matrix.obligations import COMPENSATION, RowKind
from ens_evidence.matrix.render import (
    ExportedRow,
    blockers_text,
    export_row,
    md_escape,
    review_text,
)
from ens_evidence.matrix.status import row_status
from ens_evidence.workspace import WorkspaceStore

ART_28_2 = (
    "Art. 28.2: «La relación de medidas de seguridad seleccionadas se formalizará en un "
    "documento denominado Declaración de Aplicabilidad, firmado por el responsable de la "
    "seguridad.»"
)
ART_28_3 = (
    "Art. 28.3: «Como parte integral de la Declaración de Aplicabilidad se indicará, de forma "
    "detallada, la correspondencia entre las medidas compensatorias implantadas y las medidas "
    "del anexo II que compensan.»"
)
SIGNATURE_NOTE = "El nombre del firmante es declarado; no se autentica."


class _Names:
    def __init__(self) -> None:
        self._names = {m.id: m.name for m in load_catalog().measures}

    def title(self, measure_id: str) -> str:
        name = self._names.get(measure_id)
        return md_escape(f"{measure_id} — {name}" if name else measure_id)


def _list(items: list[str], empty: str = "ninguno") -> str:
    return md_escape(", ".join(items)) if items else empty


def _header(
    declaration: Declaration, state: DeclarationState, reasons: list[str], now: datetime
) -> list[str]:
    version = declaration.catalog_version
    lines = [
        "# Declaración de Aplicabilidad",
        "",
        f"> {ART_28_2}",
        "",
        f"- Generada: {now.isoformat()} a partir de `{DECLARATION_KEY}`",
        f"- Firmante: {md_escape(declaration.approved_by)} ({role_label(declaration.role)}); "
        f"firmada el {declaration.approved_at.isoformat()}. {SIGNATURE_NOTE}",
        f"- Categoría del sistema: {declaration.category.value}",
        f"- Catálogo del anexo II: consolidado el {version.consolidation_date}, "
        f"sha256 `{version.snapshot_sha256}`",
        f"- Huellas: medidas `{declaration.measures_sha256}`, categorización "
        f"`{declaration.categorization_sha256}`, alcance `{declaration.scope_fingerprint}`",
        f"- Estado: {DECLARATION_LABELS[state]}",
    ]
    lines += [f"  - {md_escape(reason)}" for reason in reasons]
    return lines


def _compensation_of(row: ExportedRow) -> list[str]:
    return [m for o in row.obligations if o.id == COMPENSATION for m in o.compensating_measures]


def _annotation(row: ExportedRow) -> list[str]:
    owner = "sin responsable"
    if row.owner is not None:
        owner = f"{md_escape(row.owner.name)} ({role_label(row.owner.role)})"
    every = "sin periodicidad" if row.review_every_days is None else f"{row.review_every_days} días"
    review = row.last_review
    if row.kind is not RowKind.SELECTED:
        verdict = (
            "decisión de no aplicabilidad sin revisar"
            if review is None
            else f"decisión de no aplicabilidad revisada por {md_escape(review.reviewer)} "
            f"el {review.reviewed_at.date()}"
        )
    elif row.implemented and review is not None:
        substitutes = _compensation_of(row)
        by = (
            " mediante las medidas compensatorias que sustituyen a la del anexo II "
            f"(art. 28.3): {md_escape('; '.join(substitutes))};"
            if substitutes
            else ""
        )
        verdict = (
            f"implantada{by} según la revisión de {md_escape(review.reviewer)} "
            f"el {review.reviewed_at.date()}"
        )
    elif review is None:
        verdict = "sin revisar"
    else:
        verdict = "no consta como implantada"
    current = [link.target for link in (*row.technical, *row.manual) if link.counts]
    return [
        f"  - En la matriz: {verdict}",
        f"  - Responsable: {owner}; periodicidad: {every}",
        f"  - Última revisión: {review_text(review)}",
        f"  - Evidencias vigentes: {_list(current, 'ninguna')}",
        f"  - Bloqueos: {blockers_text(row)}",
    ]


class _View:
    def __init__(self, declaration: Declaration, evaluation: AssessmentResult | None) -> None:
        self.declaration = declaration
        self.names = _Names()
        self.rows = (
            {r.spec.measure_id: export_row(row_status(r), "") for r in evaluation.rows}
            if evaluation
            else {}
        )
        self.compensated = {c.replaces for c in declaration.compensations}

    def annotated(self, measure_id: str) -> list[str]:
        row = self.rows.get(measure_id)
        if row is None:
            return ["  - En la matriz: sin fila evaluada"] if self.rows else []
        return _annotation(row)

    def selected(self, measure: SelectedMeasure) -> list[str]:
        origin = (
            f"exigida por el anexo II: {md_escape(measure.required_by)}"
            if measure.required_by
            else "adicional (no la exige el anexo II)"
        )
        lines = [f"- **{self.names.title(measure.id)}** · {origin}"]
        lines.append(f"  - Refuerzos exigidos: {_list(measure.required_reinforcements)}")
        groups = "; ".join(
            "[" + " o ".join(md_escape(r) for r in group) + "]" for group in measure.alternatives
        )
        lines.append(
            f"  - Refuerzos elegidos: {_list(measure.chosen_reinforcements)}"
            + (f" (grupos alternativos {groups})" if groups else "")
        )
        if measure.id in self.compensated:
            lines.append("  - Compensada: ver «Compensaciones».")
        return [*lines, *self.annotated(measure.id)]

    def compensation(self, record: CompensationRecord) -> list[str]:
        measures = [f"  - {md_escape(m)}" for m in record.measures]
        return [
            f"### {self.names.title(record.replaces)}",
            "",
            f"- Obligaciones del anexo II que se compensan: {_list(record.obligations)}",
            "- Medidas compensatorias (art. 28.3):",
            *measures,
            *self.compensated_measure_line(record.replaces),
            f"- Justificación: {md_escape(record.justification)}",
            "",
        ]

    def compensated_measure_line(self, measure_id: str) -> list[str]:
        """Con evaluación, la línea de la matriz de la medida que se compensa."""
        row = self.rows.get(measure_id)
        return [] if row is None else [_annotation(row)[0].removeprefix("  ")]

    def not_selected(self, measure: NotSelected) -> list[str]:
        return [
            f"- **{self.names.title(measure.id)}**: {md_escape(measure.justification)}",
            *self.annotated(measure.id),
        ]

    def absent(self, measure: AbsentAssetsRecord) -> list[str]:
        return [
            f"- **{self.names.title(measure.id)}** · exigida por el anexo II: "
            f"{md_escape(measure.required_by)} · tipo de activo ausente: "
            f"{md_escape(measure.asset_type)}: {md_escape(measure.justification)}",
            *self.annotated(measure.id),
        ]

    def body(self) -> list[str]:
        d = self.declaration
        required = [m for m in d.selected if m.required_by]
        additional = [m for m in d.selected if not m.required_by]
        lines: list[str] = []

        def section(title: str, items: list, render) -> None:
            lines.extend(["", f"## {title} ({len(items)})", ""])
            for item in items:
                lines.extend(render(item))
            if not items:
                lines.append("Ninguna.")

        section("Medidas seleccionadas exigidas por el anexo II", required, self.selected)
        section("Medidas seleccionadas adicionales", additional, self.selected)
        lines.extend(["", f"## Compensaciones ({len(d.compensations)})", "", f"> {ART_28_3}", ""])
        for record in d.compensations:
            lines.extend(self.compensation(record))
        if not d.compensations:
            lines.append("Ninguna.")
        section("Medidas no seleccionadas", d.not_selected, self.not_selected)
        section(
            "Exigidas y no aplicables por ausencia de activo",
            d.required_but_absent_assets,
            self.absent,
        )
        return lines


def render_declaration(
    store: WorkspaceStore, now: datetime, *, evaluation: AssessmentResult | None = None
) -> str:
    """Texto Markdown de la Declaración; con `evaluation`, anotado con el estado en la matriz."""
    status = declaration_status(store, now)
    if status.declaration is None:
        lines = [
            "# Declaración de Aplicabilidad",
            "",
            f"- Generada: {now.isoformat()} a partir de `{DECLARATION_KEY}`",
            f"- Estado: {DECLARATION_LABELS[status.state]}",
            *(f"  - {md_escape(reason)}" for reason in status.errors),
            "",
            "No hay una Declaración firmada y legible que mostrar.",
        ]
        return "\n".join(lines) + "\n"
    view = _View(status.declaration, evaluation)
    lines = _header(status.declaration, status.state, list(status.errors), now)
    if evaluation is not None:
        lines.append(f"- Estado en la matriz evaluado el {evaluation.evaluated_at.isoformat()}")
    lines += view.body()
    return "\n".join(lines) + "\n"
