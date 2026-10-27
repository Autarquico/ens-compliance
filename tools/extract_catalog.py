"""Instantánea del anexo II -> src/ens_evidence/catalog/data/*.yaml. Sin red, determinista.

Cruza tres lugares del propio anexo: la tabla resumen, la tabla de cada medida y las líneas
«Aplicación de la medida». Cualquier diferencia o estructura desconocida aborta con la lista
completa. Lo único que no entra en el catálogo, por nombre: el prefacio de convenciones anterior
al apartado 3 y los apartados de OUT_OF_SCOPE_SECTIONS.
"""

import hashlib
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from typing import Literal

import yaml
from pydantic import ValidationError

from ens_evidence.catalog.model import (
    BASE,
    Axis,
    Catalog,
    Clause,
    Dimension,
    Group,
    LevelRequirement,
    MeasureCard,
    Reinforcement,
    Section,
    Source,
)

CATALOG_DIR = Path(__file__).resolve().parent.parent / "src/ens_evidence/catalog"
REGULATION = "Real Decreto 311/2022, de 3 de mayo"

# Divergencias de título del propio anexo (tabla resumen frente a encabezado de la medida),
# revisadas a mano sobre la instantánea. Solo se aceptan con este texto exacto del resumen; el
# nombre de la ficha es siempre el del encabezado y la divergencia queda en `notes`.
ACCEPTED_SUMMARY_TITLES = {
    "op.pl.2": "Arquitectura de Seguridad",
    "op.pl.4": "Dimensionamiento/gestión de la capacidad",
    "mp.s.4": "Protección frente a denegación de servicio",
}

# Apartados del anexo que el catálogo no recoge: valoración por niveles de madurez (6),
# desarrollo (7) e interpretación (8).
OUT_OF_SCOPE_SECTIONS = {"6", "7", "8"}

_MEASURE_ID = r"(?:org\.\d+|(?:op|mp)\.[a-z]+\.\d+)"
_HEADING = re.compile(rf"^(\d+(?:\.\d+)+)\s+(.+?)\s*\[({_MEASURE_ID})\]\.?$")
_GROUP_HEADING = re.compile(r"^(\d+)\.\s+(.+?)\s*\[(org|op|mp)\]\.?$", re.I)
_SUBGROUP_HEADING = re.compile(r"^\d+\.\d+\s+(.+?)\s*\[((?:op|mp)\.[a-z]+)\]\.?$")
_TOP_SECTION = re.compile(r"^(\d+)\.\s+\S")
_CLAUSE = re.compile(r"^(?:[–•-]\s*)?\[([^\]]+)\]\s*(.*)$", re.S)
_SUBITEM = re.compile(r"^(?:[a-z]\)|\d+\.|•)\s")
_REINFORCEMENT = re.compile(r"^Refuerzo\s+(R\d+)\s*[-–]\s*(.+?)\.?$")
_APPLICATION_HEADER = re.compile(r"^Aplicación de la medida(?: \(por (.+)\))?[.:]$")
_APPLICATION_LINE = re.compile(r"^(?:–\s*)?(Categoría|Nivel) (\w+): (.+?)\.?$")
_CELL_GROUP = re.compile(r"^\[(R\d+(?:oR\d+)+)\]$")

_ACCENTS = {"BÁSICA": "BASICA"}
_LEVEL_HEADERS = {
    Axis.CATEGORY: ("BASICA", "MEDIA", "ALTA"),
    Axis.DIMENSION: ("BAJO", "MEDIO", "ALTO"),
}
_DIMENSION_WORDS = {
    "confidencialidad": "C",
    "integridad": "I",
    "trazabilidad": "T",
    "autenticidad": "A",
    "disponibilidad": "D",
}
_INLINE = {"strong", "em", "b", "i", "sup", "sub", "span", "a", "br"}
_STRUCTURAL = {"div", "colgroup", "col", "thead", "tbody", "tr"}
_VOID = {"col", "br", "img"}


class ExtractionError(Exception):
    def __init__(self, problems: list[str]) -> None:
        super().__init__(f"{len(problems)} problemas")
        self.problems = problems


@dataclass
class Table:
    rows: list[list[str]] = field(default_factory=list)


@dataclass
class Paragraph:
    css: str
    text: str
    images: list[str]


class _Blocks(HTMLParser):
    """Aplana el HTML a párrafos y tablas; todo lo que no reconoce es un problema."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__(convert_charrefs=True)
        self.problems = problems
        self.blocks: list[Paragraph | Table] = []
        self._stack: list[str] = []
        self._text: list[str] | None = None
        self._css = ""
        self._images: list[str] = []
        self._table: Table | None = None
        self._row: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "table":
            if self._table is not None:
                self.problems.append("tabla anidada")
            self._table = Table()
        elif tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._text = []
        elif tag in ("p", "h4"):
            if self._table is None:
                self._text, self._css, self._images = [], attributes.get("class") or "", []
        elif tag == "img":
            self._images.append(attributes.get("src") or "")
        elif tag not in _INLINE | _STRUCTURAL:
            self.problems.append(f"etiqueta desconocida <{tag}>")
        if tag not in _VOID:
            self._stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID:
            return
        if not self._stack or self._stack.pop() != tag:
            self.problems.append(f"HTML desbalanceado en </{tag}>")
            return
        if tag in ("td", "th") and self._row is not None and self._text is not None:
            self._row.append(" ".join("".join(self._text).split()))
            self._text = None
        elif tag == "tr" and self._table is not None and self._row is not None:
            self._table.rows.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            self.blocks.append(self._table)
            self._table = None
        elif tag in ("p", "h4") and self._table is None and self._text is not None:
            text = " ".join("".join(self._text).split())
            self.blocks.append(Paragraph(f"{tag}.{self._css}", text, self._images))
            self._text = None

    def handle_data(self, data: str) -> None:
        if self._text is not None:
            self._text.append(data)
        elif data.strip():
            self.problems.append(f"texto fuera de párrafo o tabla: {data.strip()[:60]!r}")

    def close(self) -> None:
        super().close()
        if self._stack:
            self.problems.append(f"HTML truncado: etiquetas sin cerrar {self._stack}")


# --- expresiones de exigencia -------------------------------------------------------------


@dataclass(frozen=True)
class Expression:
    not_applicable: bool
    all_of: tuple[str, ...] = ()
    any_of: tuple[tuple[str, ...], ...] = ()

    def canonical(self) -> tuple:
        return (
            self.not_applicable,
            tuple(sorted(self.all_of)),
            tuple(sorted(sorted(g) for g in self.any_of)),
        )


def parse_expression(text: str, measure_id: str | None = None) -> Expression:
    """Celda (`aplica`, `+ R1 + [R2 o R3]`, `n.a.`, vacío) o línea de aplicación (`<id> + R1`)."""
    compact = re.sub(r"\s+", "", text)
    if compact in ("", "n.a.", "noaplica"):
        return Expression(True)
    parts = compact.split("+")
    head, rest = parts[0], parts[1:]
    if head == "aplica" and not rest:
        return Expression(False, (BASE,))
    if head and head != measure_id:
        raise ValueError(f"expresión desconocida {text!r}")
    all_of, any_of = [BASE], []
    for part in rest:
        if re.fullmatch(r"R\d+", part):
            all_of.append(part)
        elif group := _CELL_GROUP.match(part):
            any_of.append(tuple(group.group(1).split("o")))
        else:
            raise ValueError(f"expresión desconocida {text!r}")
    return Expression(False, tuple(all_of), tuple(any_of))


# --- tabla resumen ------------------------------------------------------------------------


@dataclass
class SummaryRow:
    id: str
    name: str
    axis_cell: str
    cells: list[str]


def read_summary(table: Table, problems: list[str]) -> tuple[list[SummaryRow], dict[str, str]]:
    """Filas de medida y subgrupo de cada medida (por la fila de subgrupo que las precede)."""
    rows, subgroup_of = [], {}
    subgroup = None
    for row in table.rows:
        if len(row) != 6:
            continue  # cabeceras de la tabla
        first = row[0]
        if first in ("org", "op", "mp"):
            subgroup = None
        elif re.fullmatch(r"(?:op|mp)\.[a-z]+", first):
            subgroup = first
        elif re.fullmatch(_MEASURE_ID, first):
            rows.append(SummaryRow(first, row[1], row[2], row[3:]))
            subgroup_of[first] = subgroup
        else:
            problems.append(f"tabla resumen: fila desconocida {row!r}")
    return rows, subgroup_of


# --- cuerpo de cada medida ----------------------------------------------------------------


@dataclass
class Body:
    id: str
    section: str
    name: str
    group: str | None = None
    subgroup: str | None = None
    preamble: list[str] = field(default_factory=list)
    diagrams: list[str] = field(default_factory=list)
    table: Table | None = None
    base: list[Clause] = field(default_factory=list)
    reinforcements: list[tuple[str, str, list[Clause]]] = field(default_factory=list)
    application_header: str | None = None
    application: dict[str, str] = field(default_factory=dict)


def normalize_label(label: str) -> str:
    return re.sub(r"\.{2,}", ".", re.sub(r"\s+", ".", label.strip())).rstrip(".")


@dataclass(frozen=True)
class Boundary:
    """Encabezado que abre un apartado del anexo: medida, grupo, subgrupo o apartado ajeno."""

    kind: Literal["measure", "group", "subgroup", "other"]
    id: str
    title: str = ""
    number: str = ""


def classify(block: Paragraph) -> Boundary | None:
    """None si el párrafo no es un encabezado; ValueError si lo es y no se reconoce."""
    text = block.text
    if heading := _HEADING.match(text):
        return Boundary("measure", heading.group(3), heading.group(2), heading.group(1))
    if block.css.endswith("centro_cursiva") and (top := _TOP_SECTION.match(text)):
        if group := _GROUP_HEADING.match(text):
            return Boundary("group", group.group(3).lower(), group.group(2), group.group(1))
        if top.group(1) in OUT_OF_SCOPE_SECTIONS:
            return Boundary("other", top.group(1), text)
        raise ValueError(f"apartado desconocido {text[:60]!r}")
    if subgroup := _SUBGROUP_HEADING.match(text):
        return Boundary("subgroup", subgroup.group(2), subgroup.group(1))
    if re.match(r"^\d+(?:\.\d+)*\s+.+\[(?:org|op|mp)(?:\.[a-z]+)?\]\.?$", text):
        raise ValueError(f"encabezado desconocido {text[:60]!r}")
    return None


@dataclass
class Annex:
    bodies: list[Body] = field(default_factory=list)
    groups: list[Section] = field(default_factory=list)
    subgroups: list[Section] = field(default_factory=list)


def read_annex(blocks: list[Paragraph | Table], problems: list[str]) -> Annex:
    annex = Annex()
    body: Body | None = None
    intro: Section | None = None  # grupo o subgrupo cuyo texto se está leyendo
    group: str | None = None
    subgroup: str | None = None
    clauses: list[Clause] | None = None
    in_application = False
    for block in blocks:
        if isinstance(block, Table):
            if body is not None:
                if body.table is not None:
                    problems.append(f"{body.id}: más de una tabla")
                body.table = block
            elif intro is not None:
                problems.append(f"{intro.id}: tabla en el texto de introducción")
            continue  # el resto: tabla de madurez del apartado 6
        text = block.text
        try:
            boundary = classify(block)
        except ValueError as error:
            problems.append(str(error))
            boundary = Boundary("other", "")
        if boundary and boundary.kind != "measure":
            body, clauses = None, None
            intro = None
            if boundary.kind == "group":
                intro = Section(id=boundary.id, title=boundary.title)
                annex.groups.append(intro)
                group, subgroup = boundary.id, None
            elif boundary.kind == "subgroup":
                intro = Section(id=boundary.id, title=boundary.title)
                annex.subgroups.append(intro)
                subgroup = boundary.id
            else:
                group = subgroup = None
            continue
        if boundary:
            intro = None
            body = Body(boundary.id, boundary.number, boundary.title, group, subgroup)
            annex.bodies.append(body)
            clauses, in_application = None, False
            continue
        if body is None:
            if intro is not None:
                if block.images:
                    problems.append(f"{intro.id}: imagen en el texto de introducción")
                if text:
                    _append_paragraph(intro.text, text)
            continue
        body.diagrams += block.images
        if not text:
            continue
        if text == "Requisitos.":
            clauses, in_application = body.base, False
        elif reinforcement := _REINFORCEMENT.match(text):
            clauses, in_application = [], False
            body.reinforcements.append((reinforcement.group(1), reinforcement.group(2), clauses))
        elif header := _APPLICATION_HEADER.match(text):
            body.application_header, clauses, in_application = header.group(1) or "", None, True
        elif line := _APPLICATION_LINE.match(text):
            in_application = True
            level = _ACCENTS.get(line.group(2).upper(), line.group(2).upper())
            if level in body.application:
                problems.append(f"{body.id}: nivel {level} repetido en la aplicación")
            body.application[level] = line.group(3)
        elif in_application:
            problems.append(f"{body.id}: línea inesperada tras «Aplicación»: {text[:60]!r}")
        elif clauses is None:
            _append_paragraph(body.preamble, text)
        elif clause := _CLAUSE.match(text):
            label = clause.group(1)
            normalized = normalize_label(label)
            clauses.append(
                Clause(
                    id=normalized,
                    label=None if label == normalized else label,
                    text=clause.group(2).strip(),
                )
            )
        elif _SUBITEM.match(text) and clauses:
            clauses[-1] = clauses[-1].model_copy(update={"text": f"{clauses[-1].text}\n{text}"})
        else:
            clauses.append(Clause(text=text))
    return annex


def _append_paragraph(paragraphs: list[str], text: str) -> None:
    """Un apartado «a)», «1.» o «•» continúa el párrafo anterior en vez de abrir otro."""
    if _SUBITEM.match(text) and paragraphs:
        paragraphs[-1] = f"{paragraphs[-1]}\n{text}"
    else:
        paragraphs.append(text)


def read_body_table(
    body: Body, problems: list[str]
) -> tuple[Axis, list[Dimension], list[str]] | None:
    rows = body.table.rows if body.table else []
    if len(rows) != 3 or len(rows[0]) != 2 or len(rows[1]) != 4 or len(rows[2]) != 4:
        problems.append(f"{body.id}: tabla de medida con forma desconocida")
        return None
    (_, dims), header, values = rows
    axis_word = header[0]
    if axis_word == "categoría" and dims == "Todas":
        axis, dimensions = Axis.CATEGORY, list(Dimension)
    elif axis_word == "nivel" and all(d in Dimension.__members__ for d in dims.split()):
        axis, dimensions = Axis.DIMENSION, [Dimension(d) for d in dims.split()]
    else:
        problems.append(f"{body.id}: eje desconocido {axis_word!r}/{dims!r}")
        return None
    expected = _LEVEL_HEADERS[axis]
    if tuple(_ACCENTS.get(h, h) for h in header[1:]) != expected:
        problems.append(f"{body.id}: cabecera de niveles {header[1:]} inesperada")
        return None
    return axis, dimensions, values[1:]


def dimensions_named(header: str | None) -> list[str] | None:
    if not header:
        return None
    words = re.split(r",| y | e ", header)
    letters = [_DIMENSION_WORDS.get(w.strip()) for w in words]
    return None if None in letters else letters  # type: ignore[return-value]


# --- ensamblado ---------------------------------------------------------------------------


def build_card(
    body: Body, summary: SummaryRow, subgroup: str | None, source: Source, problems: list[str]
) -> MeasureCard | None:
    parsed = read_body_table(body, problems)
    if parsed is None:
        return None
    axis, dimensions, cells = parsed
    levels_order = _LEVEL_HEADERS[axis]
    before = len(problems)

    notes = []
    if body.name != summary.name and ACCEPTED_SUMMARY_TITLES.get(body.id) == summary.name:
        notes.append(f"La tabla resumen del anexo titula la medida «{summary.name}».")
    elif body.name != summary.name:
        problems.append(f"{body.id}: título «{body.name}» ≠ tabla resumen «{summary.name}»")
    summary_axis = "Categoría" if axis is Axis.CATEGORY else " ".join(d.value for d in dimensions)
    if summary.axis_cell != summary_axis:
        problems.append(
            f"{body.id}: eje del resumen {summary.axis_cell!r} ≠ cuerpo {summary_axis!r}"
        )
    if set(body.application) != set(levels_order):
        problems.append(f"{body.id}: líneas de aplicación {sorted(body.application)} ≠ niveles")
    named = dimensions_named(body.application_header)
    if (
        body.application_header
        and axis is Axis.DIMENSION
        and named != [d.value for d in dimensions]
    ):
        problems.append(
            f"{body.id}: «aplicación (por {body.application_header})» ≠ dimensiones del cuerpo"
        )

    levels: dict[str, LevelRequirement] = {}
    for level, cell, summary_cell in zip(levels_order, cells, summary.cells, strict=True):
        try:
            expression = parse_expression(cell)
            if parse_expression(summary_cell).canonical() != expression.canonical():
                problems.append(f"{body.id} {level}: resumen {summary_cell!r} ≠ cuerpo {cell!r}")
            if level in body.application and (
                parse_expression(body.application[level], body.id).canonical()
                != expression.canonical()
            ):
                problems.append(
                    f"{body.id} {level}: aplicación {body.application[level]!r} ≠ tabla {cell!r}"
                )
        except ValueError as error:
            problems.append(f"{body.id} {level}: {error}")
            continue
        levels[level] = LevelRequirement(
            not_applicable=expression.not_applicable,
            all_of=list(expression.all_of),
            any_of=[list(g) for g in expression.any_of],
            expression=cell,
        )

    check_clause_ids(body, problems)
    if len(problems) > before or len(levels) != 3:
        return None
    reinforcements = [
        Reinforcement(
            id=rid,
            title=title,
            required_at=[level for level in levels_order if rid in levels[level].all_of],
            alternative_at=[level for level in levels_order if rid in levels[level].alternatives()],
            clauses=clauses,
        )
        for rid, title, clauses in body.reinforcements
    ]
    return MeasureCard(
        id=body.id,
        name=body.name,
        group=Group(body.id.split(".")[0]),
        subgroup=subgroup,
        applies_by=axis,
        dimensions=dimensions,
        preamble=body.preamble,
        diagram_refs=body.diagrams,
        base=body.base,
        reinforcements=reinforcements,
        levels=levels,
        notes=notes,
        source=source.model_copy(update={"section": body.section}),
    )


def check_clause_ids(body: Body, problems: list[str]) -> None:
    seen: set[str] = set()
    scopes = [("base", body.base)] + [(rid, clauses) for rid, _, clauses in body.reinforcements]
    if not body.base or any(not clauses for _, clauses in scopes):
        problems.append(f"{body.id}: base o refuerzo sin cláusulas")
    for scope, clauses in scopes:
        infix = rf"\.r{scope[1:]}" if scope != "base" else ""
        shape = re.compile(rf"{re.escape(body.id)}{infix}\.\d+(?:\.\d+)*")
        for clause in clauses:
            if clause.id is None:
                continue
            if not shape.fullmatch(clause.id):
                problems.append(
                    f"{body.id}: etiqueta {clause.label or clause.id!r} fuera de {scope}"
                )
            if clause.id in seen:
                problems.append(f"{body.id}: etiqueta repetida {clause.id}")
            seen.add(clause.id)
    ids = [rid for rid, _, _ in body.reinforcements]
    if ids != [f"R{n}" for n in range(1, len(ids) + 1)]:
        problems.append(f"{body.id}: refuerzos no correlativos {ids}")


def extract(snapshot: bytes, meta: dict) -> Catalog:
    problems: list[str] = []
    digest = hashlib.sha256(snapshot).hexdigest()
    if digest != meta["snapshot_sha256"]:
        raise ExtractionError([f"sha256 de la instantánea {digest} ≠ metadatos"])
    parser = _Blocks(problems)
    parser.feed(snapshot.decode("utf-8"))
    parser.close()
    blocks = parser.blocks
    tables = [b for b in blocks if isinstance(b, Table)]
    if not tables or not tables[0].rows or tables[0].rows[0][0] != "Medidas de Seguridad":
        raise ExtractionError([*problems, "no se encuentra la tabla resumen"])
    summary, subgroup_of = read_summary(tables[0], problems)
    summary_by_id = {row.id: row for row in summary}
    annex = read_annex(blocks[blocks.index(tables[0]) + 1 :], problems)
    bodies = annex.bodies

    if len(summary_by_id) != len(summary):
        problems.append("ids repetidos en la tabla resumen")
    body_ids = [b.id for b in bodies]
    if len(set(body_ids)) != len(body_ids):
        problems.append("ids repetidos en los cuerpos de medida")
    for missing in sorted(set(summary_by_id) - set(body_ids)):
        problems.append(f"{missing}: está en la tabla resumen y no tiene cuerpo")
    for extra in sorted(set(body_ids) - set(summary_by_id)):
        problems.append(f"{extra}: tiene cuerpo y no está en la tabla resumen")
    for body in bodies:
        if body.group != body.id.split(".")[0] or body.subgroup != subgroup_of.get(body.id):
            problems.append(
                f"{body.id}: el apartado {body.group}/{body.subgroup} no casa con la tabla resumen "
                f"({subgroup_of.get(body.id)})"
            )
    if body_ids != [row.id for row in summary] and not problems:
        problems.append("el orden de las medidas difiere entre resumen y cuerpos")

    source = Source(
        regulation=REGULATION,
        annex="II",
        section="",
        url=meta["url"],
        consolidation_date=date.fromisoformat(meta["consolidation_date"]),
        snapshot_sha256=digest,
    )
    cards = []
    for body in bodies:
        if body.id in summary_by_id:
            card = build_card(body, summary_by_id[body.id], subgroup_of[body.id], source, problems)
            if card:
                cards.append(card)
    if problems:
        raise ExtractionError(problems)
    try:
        return Catalog(groups=annex.groups, subgroups=annex.subgroups, measures=cards)
    except ValidationError as error:
        raise ExtractionError([str(e["msg"]) for e in error.errors()]) from None


def write_data(catalog: Catalog, directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for stale in directory.glob("*.yaml"):
        stale.unlink()
    for group in Group:
        document = {
            "group": catalog.group(group).model_dump(mode="json"),
            "subgroups": [
                s.model_dump(mode="json")
                for s in catalog.subgroups
                if s.id.startswith(f"{group.value}.")
            ],
            "measures": [c.model_dump(mode="json") for c in catalog.measures if c.group is group],
        }
        text = yaml.safe_dump(document, sort_keys=False, allow_unicode=True, width=100)
        (directory / f"{group.value}.yaml").write_text(text, encoding="utf-8")


def main() -> int:
    source_dir = CATALOG_DIR / "source"
    meta = yaml.safe_load((source_dir / "anexo-ii.meta.yaml").read_text(encoding="utf-8"))
    try:
        catalog = extract((source_dir / meta["snapshot"]).read_bytes(), meta)
    except ExtractionError as error:
        print("extracción abortada:", file=sys.stderr)
        for problem in dict.fromkeys(error.problems):
            print(f"  - {problem}", file=sys.stderr)
        return 1
    write_data(catalog, CATALOG_DIR / "data")
    print(f"{len(catalog.measures)} medidas escritas en {CATALOG_DIR / 'data'}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
