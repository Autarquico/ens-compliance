"""Renderizador estricto: interpolación y listas, sin lógica. Mismas entradas, mismos bytes."""

import re

from ens_evidence.orgdocs.answers import NotApplicable, Value, condition_met
from ens_evidence.orgdocs.template import (
    Line,
    LoadedTemplate,
    Node,
    substitute,
    variables,
)

RENDERER_VERSION = 1
_SPECIAL = re.compile(r"([\\`*_\[\]<>|~&])")
_BLOCK_START = re.compile(r"^(\s*)([#+=-])")
_ORDERED = re.compile(r"^(\s*\d+)([.)])")
_LIST_ITEM = re.compile(r"^\s*([-*+]|\d+[.)])\s")


def escape_markdown(text: str) -> str:
    """Neutraliza el Markdown de una respuesta: se muestra tal cual, no se interpreta.
    Sin sangría, que Markdown leería como bloque de código."""
    lines = []
    for line in text.split("\n"):
        line = _SPECIAL.sub(r"\\\1", line.lstrip())
        line = _BLOCK_START.sub(r"\1\\\2", line)
        lines.append(_ORDERED.sub(r"\1\\\2", line))
    return "\n".join(lines)


def _not_applicable_line(value: NotApplicable, label: str) -> str:
    return f"{label}: {escape_markdown(value.reason)}"


def render_nodes(nodes: list[Node], values: dict[str, Value], labels: dict[str, str]) -> str:
    out: list[str] = []
    for node in nodes:
        if isinstance(node, Line):
            inline = bool(_LIST_ITEM.match(node.text))
            scalars = {
                name: _scalar(values[name], inline, labels[name]) for name in variables(node.text)
            }
            out.append(substitute(node.text, scalars))
            continue
        value = values[node.variable]
        if isinstance(value, NotApplicable):
            out.append(_not_applicable_line(value, labels[node.variable]))
        elif isinstance(value, list):
            for item in value:
                out.extend(_item_line(line, item) for line in node.lines)
    return "\n".join(out)


def _scalar(value: Value, inline: bool, label: str) -> str:
    """En un elemento de lista el texto va en una sola línea: un salto lo cerraría."""
    if isinstance(value, NotApplicable):
        return _not_applicable_line(value, label)
    assert isinstance(value, str)
    return escape_markdown(" ".join(value.split()) if inline else value)


def _item_line(line: str, item: str | dict[str, str]) -> str:
    if isinstance(item, str):
        return substitute(line, {".": escape_markdown(item)})
    return substitute(line, {f".{key}": escape_markdown(text) for key, text in item.items()})


def render_document(loaded: LoadedTemplate, values: dict[str, Value]) -> list[tuple[str, str]]:
    """(id de sección, cuerpo renderizado) en el orden de la plantilla; omite las secciones
    cuya condición `when` no se cumple."""
    labels = {q.id: q.not_applicable_label for q in loaded.template.questions}
    return [
        (section.id, render_nodes(loaded.bodies[section.id], values, labels).rstrip())
        for section in loaded.template.sections
        if section.when is None or condition_met(section.when, values)
    ]
