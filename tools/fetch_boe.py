"""Descarga el texto consolidado del RD 311/2022 y guarda la instantánea del anexo II.

Única pieza del catálogo que usa red. Salida en src/ens_evidence/catalog/source/:
anexo-ii.html (solo el anexo II) y anexo-ii.meta.yaml (url, fecha de consolidación, sha256).
"""

import hashlib
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
import yaml

URL = "https://www.boe.es/eli/es/rd/2022/05/03/311/con"
SOURCE_DIR = Path(__file__).resolve().parent.parent / "src/ens_evidence/catalog/source"
SNAPSHOT = "anexo-ii.html"
META = "anexo-ii.meta.yaml"

_START = '<div class="bloque" id="ai-2">'
_END = '<div class="bloque" id="ai-3">'
_CONSOLIDATION = re.compile(r"Última actualización publicada el (\d{2})/(\d{2})/(\d{4})")


def extract_annex_ii(page: str) -> str:
    start, end = page.find(_START), page.find(_END)
    if start < 0 or end < start:
        raise SystemExit("no se encuentra el anexo II en la página; ¿ha cambiado la estructura?")
    if "ANEXO II" not in page[start:end] or "mp.s.4" not in page[start:end]:
        raise SystemExit("el anexo II descargado parece truncado")
    return page[start:end]


def consolidation_date(page: str) -> str:
    found = {m.groups() for m in _CONSOLIDATION.finditer(page)}
    if len(found) != 1:
        raise SystemExit(f"fecha de consolidación no determinable: {sorted(found)}")
    day, month, year = found.pop()
    return f"{year}-{month}-{day}"


def main() -> None:
    response = httpx.get(
        URL, follow_redirects=True, timeout=60, headers={"User-Agent": "ens-evidence"}
    )
    response.raise_for_status()
    page = response.text
    annex = extract_annex_ii(page)
    data = annex.encode("utf-8")
    meta = {
        "url": URL,
        "consolidation_date": consolidation_date(page),
        "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "snapshot": SNAPSHOT,
        "snapshot_sha256": hashlib.sha256(data).hexdigest(),
        "snapshot_bytes": len(data),
    }
    SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    (SOURCE_DIR / SNAPSHOT).write_bytes(data)
    (SOURCE_DIR / META).write_text(yaml.safe_dump(meta, sort_keys=False), encoding="utf-8")
    print(
        f"{SNAPSHOT}: {len(data)} bytes, consolidado {meta['consolidation_date']}", file=sys.stderr
    )


if __name__ == "__main__":
    main()
