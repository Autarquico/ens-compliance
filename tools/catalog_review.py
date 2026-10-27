"""Informe HTML de revisión humana: texto del anexo II frente a la ficha de cada medida.

Uso: python tools/catalog_review.py INFORME.html
El informe lleva texto de la instantánea; escríbelo fuera del repo. Sin red.
"""

import argparse
import hashlib
import sys
from html import escape
from pathlib import Path

import extract_catalog as extractor
import yaml

from ens_evidence.catalog import catalog_sha256, load_catalog, review_status
from ens_evidence.catalog.model import Catalog, Clause, MeasureCard, Reinforcement, Section

STYLE = """
body{font:15px/1.5 system-ui,sans-serif;margin:0 auto;max-width:1400px;padding:24px;color:#1a1d23}
h1{font-size:22px}h2{font-size:18px;margin:0 0 8px}h3{font-size:14px;margin:16px 0 4px}
section.measure,section.intro{border-top:2px solid #1a1d23;margin-top:32px;padding-top:12px}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:24px}
.col{min-width:0}.col>h3:first-child{margin-top:0;color:#555;text-transform:uppercase}
table{border-collapse:collapse;margin:8px 0}
td,th{border:1px solid #c5c9d1;padding:4px 8px;vertical-align:top}
.divergences{background:#fff4d6;border:1px solid #d9a400;padding:8px 16px}
.clause{margin:4px 0;white-space:pre-line}.id{font:12px monospace;color:#555}
code{background:#eef0f3;padding:0 4px}.muted{color:#666}
nav{columns:4;font:13px monospace}
"""


def source_segments(snapshot: bytes) -> dict[str, list[extractor.Paragraph | extractor.Table]]:
    """Bloques del anexo de cada medida, grupo y subgrupo, segmentados como lo hace el extractor."""
    problems: list[str] = []
    parser = extractor._Blocks(problems)
    parser.feed(snapshot.decode("utf-8"))
    parser.close()
    segments: dict[str, list[extractor.Paragraph | extractor.Table]] = {}
    current: list[extractor.Paragraph | extractor.Table] | None = None
    blocks = parser.blocks
    first_table = next(i for i, b in enumerate(blocks) if isinstance(b, extractor.Table))
    for block in blocks[first_table + 1 :]:
        if isinstance(block, extractor.Paragraph):
            try:
                boundary = extractor.classify(block)
            except ValueError as error:
                problems.append(str(error))
                boundary = None
            if boundary:
                current = segments.setdefault(boundary.id, []) if boundary.kind != "other" else None
        if current is not None:
            current.append(block)
    if problems:
        raise SystemExit("la instantánea no se puede leer: " + "; ".join(problems))
    return segments


def render_source(blocks: list[extractor.Paragraph | extractor.Table]) -> str:
    parts = []
    for block in blocks:
        if isinstance(block, extractor.Table):
            rows = "".join(
                "<tr>" + "".join(f"<td>{escape(cell)}</td>" for cell in row) + "</tr>"
                for row in block.rows
            )
            parts.append(f"<table>{rows}</table>")
            continue
        if block.text:
            parts.append(f"<p class=clause>{escape(block.text)}</p>")
        parts += [f"<p class=muted>[imagen: {escape(src)}]</p>" for src in block.images]
    return "\n".join(parts)


def render_clauses(clauses: list[Clause]) -> str:
    return "\n".join(
        f"<p class=clause><span class=id>{escape(c.label or c.id or '·')}</span> "
        f"{escape(c.text)}</p>"
        for c in clauses
    )


def render_card(card: MeasureCard) -> str:
    levels = "".join(
        f"<tr><th>{escape(level)}</th><td>{escape(req.expression or '(celda vacía)')}</td>"
        f"<td><code>{escape(_structured(req.not_applicable, req.all_of, req.any_of))}</code>"
        "</td></tr>"
        for level, req in card.levels.items()
    )
    reinforcements = "".join(
        f"<h3>{escape(r.id)} — {escape(r.title)} "
        f"<span class=muted>({escape(_reinforcement_levels(r))})</span>"
        f"</h3>{render_clauses(r.clauses)}"
        for r in card.reinforcements
    )
    sections = [
        f"<h3>Eje y dimensiones</h3><p>Por <b>{card.applies_by.value}</b>; dimensiones: "
        f"{escape(', '.join(d.value for d in card.dimensions))}</p>",
        "<h3>Niveles (expresión original · estructurada)</h3>"
        f"<table><tr><th>Nivel</th><th>Anexo</th><th>Ficha</th></tr>{levels}</table>",
    ]
    if card.preamble:
        sections.append(
            "<h3>Preámbulo</h3>"
            + "".join(f"<p class=clause>{escape(p)}</p>" for p in card.preamble)
        )
    sections.append("<h3>Base</h3>" + render_clauses(card.base))
    sections.append(reinforcements)
    if card.notes:
        sections.append("<h3>Notas</h3>" + "".join(f"<p>{escape(n)}</p>" for n in card.notes))
    return "\n".join(sections)


def _reinforcement_levels(reinforcement: Reinforcement) -> str:
    shown = [
        f"{label}: {', '.join(levels)}"
        for label, levels in (
            ("exigido en", reinforcement.required_at),
            ("alternativa en", reinforcement.alternative_at),
        )
        if levels
    ]
    return "; ".join(shown) or "ningún nivel"


def _structured(not_applicable: bool, all_of: list[str], any_of: list[list[str]]) -> str:
    if not_applicable:
        return "not_applicable"
    alternatives = [f"any_of({', '.join(group)})" for group in any_of]
    return " + ".join([f"all_of({', '.join(all_of)})", *alternatives])


def render_intro(section: Section, blocks: list[extractor.Paragraph | extractor.Table]) -> str:
    text = "".join(f"<p class=clause>{escape(p)}</p>" for p in section.text)
    return (
        f"<section class=intro id='{escape(section.id)}'>"
        f"<h2>{escape(section.id)} — {escape(section.title)}</h2>"
        f"<div class=cols><div class=col><h3>Texto del anexo</h3>{render_source(blocks)}</div>"
        f"<div class=col><h3>Texto de introducción en el catálogo</h3>"
        f"{text or '<p class=muted>(sin texto)</p>'}</div></div></section>"
    )


def render_report(
    catalog: Catalog, segments: dict[str, list[extractor.Paragraph | extractor.Table]]
) -> str:
    cards = catalog.measures
    divergent = [c for c in cards if c.notes]
    status = review_status()
    head = (
        f"<h1>Revisión del catálogo — anexo II, RD 311/2022</h1>"
        f"<p>Consolidado {cards[0].source.consolidation_date} · instantánea "
        f"<code>{cards[0].source.snapshot_sha256}</code><br>"
        f"catálogo <code>{catalog_sha256()}</code>"
        f" · estado de la revisión: {escape(status.message)}</p>"
    )
    divergences = (
        "<div class=divergences><h2>Divergencias de título del propio anexo</h2>"
        + "".join(
            f"<p><b>{escape(c.id)}</b>: {''.join(escape(n) for n in c.notes)}</p>"
            for c in divergent
        )
        + "</div>"
        if divergent
        else ""
    )
    nav = (
        "<nav>"
        + "".join(f"<a href='#{escape(c.id)}'>{escape(c.id)}</a><br>" for c in cards)
        + "</nav>"
    )
    body: list[str] = []
    shown: set[str] = set()
    for c in cards:
        for section in (catalog.group(c.group), catalog.subgroup(c.subgroup)):
            if section and section.id not in shown:
                shown.add(section.id)
                body.append(render_intro(section, segments.get(section.id, [])))
        body.append(
            f"<section class=measure id='{escape(c.id)}'><h2>{escape(c.id)} — {escape(c.name)}</h2>"
            f"<div class=cols><div class=col><h3>Texto del anexo</h3>"
            f"{render_source(segments.get(c.id, []))}</div>"
            f"<div class=col><h3>Ficha</h3>{render_card(c)}</div></div></section>"
        )
    return (
        f"<!doctype html><html lang=es><meta charset=utf-8><title>Revisión del catálogo</title>"
        f"<style>{STYLE}</style><body>{head}{divergences}{nav}{''.join(body)}</body></html>"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="Ruta del informe HTML (fuera del repo).")
    args = parser.parse_args()
    source_dir = extractor.CATALOG_DIR / "source"
    meta = yaml.safe_load((source_dir / "anexo-ii.meta.yaml").read_text(encoding="utf-8"))
    snapshot = (source_dir / meta["snapshot"]).read_bytes()
    catalog = load_catalog()
    cards = catalog.measures
    if hashlib.sha256(snapshot).hexdigest() != cards[0].source.snapshot_sha256:
        print(
            "la instantánea no corresponde al catálogo; regenera con extract_catalog",
            file=sys.stderr,
        )
        return 1
    segments = source_segments(snapshot)
    missing = [
        i
        for i in (*(c.id for c in cards), *(s.id for s in (*catalog.groups, *catalog.subgroups)))
        if not segments.get(i)
    ]
    if missing:
        print(
            f"secciones sin texto fuente en la instantánea: {', '.join(missing)}", file=sys.stderr
        )
        return 1
    args.output.write_text(render_report(catalog, segments), encoding="utf-8")
    print(f"{len(cards)} medidas en {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
