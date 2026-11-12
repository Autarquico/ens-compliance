"""Ayuda: runbooks servidos en solo lectura desde el paquete.

La copia de `ui/runbooks/` debe ser idéntica a `docs/runbooks/` (lo comprueba un test); se
actualiza con `cp`. El Markdown se reduce a bloques de datos y la plantilla los escapa: nunca se
emite HTML construido a mano.
"""

import re
import textwrap
from functools import cache
from pathlib import Path
from typing import Any

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import BaseRoute, Route

from ens_evidence.ui.pages import error_page, page_context, render

RUNBOOKS_DIR = Path(__file__).resolve().parent.parent / "runbooks"
RUNBOOKS = frozenset({"kit-copias", "kit-claves", "kit-contrasenas", "restauracion-de-evidencias"})

_HEADING = re.compile(r"(#{1,6}) (.+)")
_BULLET = re.compile(r"- (.*)")
_NUMBERED = re.compile(r"(\d+)\. (.*)")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)]*)\)")
_RUNBOOK_TARGET = re.compile(r"([a-z0-9-]+)\.md")
_CODE = re.compile(r"`([^`]*)`")


def _runbook_href(target: str) -> str | None:
    named = _RUNBOOK_TARGET.fullmatch(target)
    return f"/ayuda/{named[1]}" if named and named[1] in RUNBOOKS else None


def _inline(text: str) -> list[dict[str, Any]]:
    """Segmentos que la plantilla escapa: `código` y enlaces a otros runbooks de esta ayuda.

    Cualquier otro enlace y las negritas quedan en texto plano.
    """
    parts: list[dict[str, Any]] = []
    position = 0
    for link in [*_LINK.finditer(text), None]:
        end = link.start() if link else len(text)
        plain = text[position:end].replace("**", "")
        parts += [{"code": i % 2 == 1, "text": t} for i, t in enumerate(_CODE.split(plain)) if t]
        if link:
            href = _runbook_href(link[2])
            label = link[1].replace("**", "")
            parts.append({"code": False, "text": label, **({"href": href} if href else {})})
            position = link.end()
    return parts


def parse_markdown(source: str) -> list[dict[str, Any]]:
    """Títulos, párrafos, listas y bloques de código; lo demás, como párrafo."""
    blocks: list[dict[str, Any]] = []
    paragraph: list[str] = []
    items: list[str] | None = None
    fence: list[str] | None = None

    def flush() -> None:
        if paragraph:
            blocks.append({"kind": "p", "inline": _inline(" ".join(paragraph))})
            paragraph.clear()

    def start_list(kind: str, start: int = 1) -> list[str]:
        nonlocal items
        flush()
        items = []
        blocks.append({"kind": kind, "start": start, "items": items})
        return items

    for line in source.splitlines():
        if line.strip().startswith("```"):
            if fence is None:
                flush()
                fence = []
            else:
                blocks.append({"kind": "pre", "text": textwrap.dedent("\n".join(fence))})
                fence, items = None, None
            continue
        if fence is not None:
            fence.append(line)
            continue
        heading, bullet, numbered = (
            _HEADING.fullmatch(line),
            _BULLET.fullmatch(line),
            _NUMBERED.fullmatch(line),
        )
        if not line.strip():
            flush()
        elif heading:
            flush()
            items = None
            blocks.append(
                {
                    "kind": "h",
                    "level": len(heading[1]),
                    "inline": _inline(heading[2]),
                }
            )
        elif bullet or numbered:
            kind, text = ("ul", bullet[1]) if bullet else ("ol", numbered[2])  # type: ignore[index]
            if items is None or blocks[-1]["kind"] != kind:
                start_list(kind, int(numbered[1]) if numbered else 1)
            assert items is not None
            items.append(text)
        elif items and line[:1] == " " and blocks[-1]["kind"] in ("ul", "ol"):
            items[-1] += " " + line.strip()
        else:
            paragraph.append(line.strip())
    flush()
    for block in blocks:
        if block["kind"] in ("ul", "ol"):
            block["items"] = [_inline(i) for i in block["items"]]
    return blocks


@cache
def _runbook(name: str) -> dict[str, Any]:
    blocks = parse_markdown((RUNBOOKS_DIR / f"{name}.md").read_text(encoding="utf-8"))
    title = next((b["inline"][0]["text"] for b in blocks if b["kind"] == "h"), name)
    return {"title": title, "blocks": blocks}


def runbook(request: Request) -> Response:
    name = request.path_params["name"]
    if name not in RUNBOOKS or not (RUNBOOKS_DIR / f"{name}.md").is_file():
        return error_page(request, 404)
    doc = _runbook(name)
    return render(
        request,
        "views/ayuda.html",
        **page_context(request, "herramientas", doc["title"]),
        title=doc["title"],
        blocks=doc["blocks"][1:],
    )


HELP_ROUTES: list[BaseRoute] = [Route("/ayuda/{name}", runbook, methods=["GET"])]
