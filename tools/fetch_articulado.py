"""Descarga el RD 311/2022 consolidado y extrae los apartados del articulado citados.

Salida en src/ens_evidence/orgdocs/sources/: articulado.html (solo los bloques citados, tal cual
los sirve el BOE) y articulado.yaml (texto literal de cada apartado, url, fecha de consolidación y
sha256 de la instantánea). La extracción es determinista: `extract_clauses(snapshot)` reproduce
el yaml sin red. Con --offline se regenera el yaml desde la instantánea guardada.
"""

import argparse
import hashlib
import re
import sys
from html import unescape
from pathlib import Path

import fetch_boe
import httpx
import yaml

SOURCE_DIR = Path(__file__).resolve().parent.parent / "src/ens_evidence/orgdocs/sources"
SNAPSHOT = "articulado.html"
DATA = "articulado.yaml"
REGULATION = "Real Decreto 311/2022, de 3 de mayo"
ARTICLES = (2, 11, 12, 13, 15, 25, 33)
ANNEX_III_ID = "ai-3"

_PARAGRAPH = re.compile(r'<p class="[^"]*">(.*?)</p>', re.DOTALL)
_TAG = re.compile(r"<[^>]+>")
_BLOCK = '<div class="bloque"'
_NUMBERED = re.compile(r"^\d+\.\d+ |^\d+\. ")
_LETTER = re.compile(r"^([a-zñ])\) ")

# (id, referencia, bloque, apartado, letra)
SELECTION = (
    ("art.2.3", "RD 311/2022, art. 2.3", "art2", "3", None),
    ("art.11", "RD 311/2022, art. 11", "art11", None, None),
    ("art.11.3", "RD 311/2022, art. 11.3", "art11", "3", None),
    ("art.12.1", "RD 311/2022, art. 12.1", "art12", "1", None),
    ("art.12.1.f", "RD 311/2022, art. 12.1.f", "art12", "1", "f"),
    ("art.12.2", "RD 311/2022, art. 12.2", "art12", "2", None),
    ("art.13.2", "RD 311/2022, art. 13.2", "art13", "2", None),
    ("art.13.3", "RD 311/2022, art. 13.3", "art13", "3", None),
    ("art.13.5", "RD 311/2022, art. 13.5", "art13", "5", None),
    ("art.15.2", "RD 311/2022, art. 15.2", "art15", "2", None),
    ("art.25.1", "RD 311/2022, art. 25.1", "art25", "1", None),
    ("art.25.2", "RD 311/2022, art. 25.2", "art25", "2", None),
    ("art.33.7", "RD 311/2022, art. 33.7", "art33", "7", None),
    ("anexo-iii.1.1.f", "RD 311/2022, anexo III, 1.1.f", "anexo3", "1.1", "f"),
)


def _blocks(page: str) -> list[str]:
    starts = [m.start() for m in re.finditer(re.escape(_BLOCK), page)]
    return [page[s:e] for s, e in zip(starts, [*starts[1:], len(page)], strict=True)]


def extract_snapshot(page: str) -> str:
    """Los bloques de los artículos citados y el anexo III, en el orden de la norma."""
    wanted: list[str] = []
    for block in _blocks(page):
        heading = re.search(r'<h5 class="articulo">Artículo (\d+)\.', block)
        if heading and int(heading.group(1)) in ARTICLES:
            wanted.append(block.rstrip())
        elif f'id="{ANNEX_III_ID}"' in block[:60] and "ANEXO III" in block:
            wanted.append(block.rstrip())
    if len(wanted) != len(ARTICLES) + 1:
        raise SystemExit(f"se esperaban {len(ARTICLES) + 1} bloques y hay {len(wanted)}")
    return "\n".join(wanted) + "\n"


def _paragraphs(block: str) -> list[str]:
    return [" ".join(unescape(_TAG.sub("", raw)).split()) for raw in _PARAGRAPH.findall(block)]


def _article_blocks(snapshot: str) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for block in _blocks(snapshot):
        heading = re.search(r'<h5 class="articulo">Artículo (\d+)\.', block)
        if heading:
            found[f"art{heading.group(1)}"] = _paragraphs(block)
        elif "ANEXO III" in block:
            found["anexo3"] = _paragraphs(block)
    return found


def _next_apartado(number: str) -> str:
    """Marca del apartado siguiente: `4. ` tras `3`, `1.2 ` tras `1.1`."""
    if "." in number:
        head, tail = number.rsplit(".", 1)
        return f"{head}.{int(tail) + 1} "
    return f"{int(number) + 1}. "


def _apartado(paragraphs: list[str], number: str) -> list[str]:
    """Párrafos del apartado `number` (`3` en un artículo, `1.1` en el anexo) hasta el siguiente
    esperado. Otro apartado numerado antes del esperado indica una estructura desconocida."""
    prefix = f"{number}. " if "." not in number else f"{number} "
    starts = [i for i, p in enumerate(paragraphs) if p.startswith(prefix)]
    if len(starts) != 1:
        raise SystemExit(f"el apartado {number} aparece {len(starts)} veces")
    following = _next_apartado(number)
    end = len(paragraphs)
    for i in range(starts[0] + 1, len(paragraphs)):
        if paragraphs[i].startswith(following):
            end = i
            break
        if _NUMBERED.match(paragraphs[i]):
            raise SystemExit(f"tras el apartado {number} aparece «{paragraphs[i][:30]}»")
    return paragraphs[starts[0] : end]


def _letter(paragraphs: list[str], letter: str) -> list[str]:
    found = [p for p in paragraphs if (m := _LETTER.match(p)) and m.group(1) == letter]
    if len(found) != 1:
        raise SystemExit(f"el apartado no tiene una única letra {letter}")
    return found


def extract_clauses(snapshot: str) -> list[dict[str, str]]:
    blocks = _article_blocks(snapshot)
    clauses = []
    for clause_id, reference, block, apartado, letter in SELECTION:
        if block not in blocks:
            raise SystemExit(f"falta el bloque {block} en la instantánea")
        paragraphs = blocks[block]
        if apartado is not None:
            paragraphs = _apartado(paragraphs, apartado)
        if letter is not None:
            paragraphs = _letter(paragraphs, letter)
        clauses.append({"id": clause_id, "reference": reference, "text": "\n\n".join(paragraphs)})
    return clauses


def write(snapshot: str, consolidation_date: str, url: str) -> None:
    data = snapshot.encode("utf-8")
    document = {
        "regulation": REGULATION,
        "url": url,
        "consolidation_date": consolidation_date,
        "snapshot": SNAPSHOT,
        "snapshot_sha256": hashlib.sha256(data).hexdigest(),
        "clauses": extract_clauses(snapshot),
    }
    SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    (SOURCE_DIR / SNAPSHOT).write_bytes(data)
    text = yaml.safe_dump(document, sort_keys=False, allow_unicode=True, width=100)
    (SOURCE_DIR / DATA).write_text(text, encoding="utf-8")
    print(
        f"{DATA}: {len(document['clauses'])} apartados, consolidado {consolidation_date}",
        file=sys.stderr,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--offline", action="store_true", help="regenera desde la instantánea")
    parser.add_argument("--page", type=Path, help="página consolidada del BOE ya descargada")
    args = parser.parse_args()
    if args.offline:
        previous = yaml.safe_load((SOURCE_DIR / DATA).read_text(encoding="utf-8"))
        snapshot = (SOURCE_DIR / SNAPSHOT).read_text(encoding="utf-8")
        write(snapshot, previous["consolidation_date"], previous["url"])
        return
    if args.page:
        page = args.page.read_text(encoding="utf-8")
    else:
        response = httpx.get(
            fetch_boe.URL, follow_redirects=True, timeout=60, headers={"User-Agent": "ens-evidence"}
        )
        response.raise_for_status()
        page = response.text
    write(extract_snapshot(page), fetch_boe.consolidation_date(page), fetch_boe.URL)


if __name__ == "__main__":
    main()
