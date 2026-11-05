"""Particiones del banco: la reservada no repite textos de defecto ni de evidencia de la de
desarrollo. Lee los casos con los cargadores, sin `open_partition`: no anota mediciones."""

import re
from collections.abc import Iterable, Iterator
from pathlib import Path

from ens_evidence.benchmark.access import MEASUREMENTS_FILE
from ens_evidence.benchmark.cases import TEXT_SUFFIXES, case_files_dir, case_paths, load_case
from ens_evidence.benchmark.model import (
    Case,
    Compensate,
    DeclareNotApplicable,
    Partition,
    SetAnswer,
)

BENCH = Path(__file__).resolve().parents[2] / "benchmark"
SHINGLE = 8
"""Palabras seguidas que dos textos no pueden compartir: más que una frase hecha."""
MIN_WORDS = 4
"""Los valores más cortos («No se registra») coinciden sin haberse copiado."""
_WORD = re.compile(r"\w+")

Text = tuple[str, str]
"""Dónde está (`<partición>/<caso> › <campo>`) y el texto."""


def words(text: str) -> tuple[str, ...]:
    return tuple(w.casefold() for w in _WORD.findall(text))


def shingles(text: str) -> set[tuple[str, ...]]:
    tokens = words(text)
    return {tokens[i : i + SHINGLE] for i in range(len(tokens) - SHINGLE + 1)}


def case_texts(root: Path, case: Case) -> Iterator[Text]:
    """Lo que describe el defecto o hace de evidencia: razones, textos de las mutaciones y los
    ficheros de texto del caso. Las citas del anexo II se repiten por diseño y no cuentan."""
    where = case.key
    for defect in case.defects:
        yield f"{where} › {defect.id}.rationale", defect.rationale
    for index, mutation in enumerate(case.mutations):
        field = f"{where} › mutations[{index}]"
        if isinstance(mutation, DeclareNotApplicable | Compensate):
            yield f"{field}.justification", mutation.justification
        if isinstance(mutation, Compensate):
            yield from ((f"{field}.measures", m) for m in mutation.measures)
        if isinstance(mutation, SetAnswer):
            values = mutation.value if isinstance(mutation.value, list) else [mutation.value]
            yield from ((f"{field}.value", v) for v in values if isinstance(v, str))
    files = case_files_dir(root, case)
    if files.is_dir():
        for path in sorted(p for p in files.rglob("*") if p.suffix.lower() in TEXT_SUFFIXES):
            yield f"{where} › files/{path.relative_to(files).as_posix()}", path.read_text("utf-8")


def partition_texts(root: Path, partition: Partition) -> list[Text]:
    return [
        text
        for path in case_paths(root, partition)
        for text in case_texts(root, load_case(path, root))
    ]


def overlaps(holdout: Iterable[Text], dev: Iterable[Text]) -> list[str]:
    """Cada texto de la reservada igual a uno de desarrollo (sin contar mayúsculas, espacios ni
    puntuación) o que comparte con él `SHINGLE` palabras seguidas."""
    dev = list(dev)
    whole = {words(text): where for where, text in dev if len(words(text)) >= MIN_WORDS}
    pieces = {piece: where for where, text in dev for piece in shingles(text)}
    found = []
    for where, text in holdout:
        twin = whole.get(words(text)) or next(
            (pieces[p] for p in sorted(shingles(text)) if p in pieces), None
        )
        if twin is not None:
            found.append(f"{where} repite {twin}")
    return found


def test_la_reservada_no_repite_textos_de_la_de_desarrollo():
    ledger = BENCH / MEASUREMENTS_FILE
    before = ledger.read_bytes() if ledger.exists() else None
    holdout = partition_texts(BENCH, Partition.HOLDOUT)

    found = overlaps(holdout, partition_texts(BENCH, Partition.DEV))

    assert holdout, "la reservada no tiene textos que comparar"
    assert found == []
    assert (ledger.read_bytes() if ledger.exists() else None) == before


def test_el_solape_ve_un_texto_copiado_aunque_cambien_mayusculas_espacios_y_puntuacion():
    dev = [("dev/a › x.rationale", "El inventario aportado no nombra a ningún responsable.")]
    copied = [
        ("holdout/b › y.rationale", "el  inventario aportado, NO nombra a ningún responsable")
    ]

    assert overlaps(copied, dev) == ["holdout/b › y.rationale repite dev/a › x.rationale"]


def test_el_solape_ve_un_fragmento_largo_copiado_dentro_de_otro_texto():
    dev = [
        (
            "dev/a › files/acta.txt",
            "Las copias nocturnas terminaron bien y se probó la restauración.",
        )
    ]
    reused = [
        (
            "holdout/b › z.rationale",
            "Dice que las copias nocturnas terminaron bien y se probó otra.",
        )
    ]

    assert overlaps(reused, dev) == ["holdout/b › z.rationale repite dev/a › files/acta.txt"]


def test_el_solape_no_salta_por_valores_cortos_ni_por_temas_comunes():
    dev = [("dev/a › m.value", "No se registra"), ("dev/a › r", "Las copias no se prueban nunca.")]
    other = [
        ("holdout/b › m.value", "No se registra"),
        ("holdout/b › r", "Nadie prueba las copias."),
    ]

    assert overlaps(other, dev) == []
