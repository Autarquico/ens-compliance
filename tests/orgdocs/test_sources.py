import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

from ens_evidence.catalog import ReviewState
from ens_evidence.orgdocs import sources

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def fetcher():
    tools = REPO / "tools"
    sys.path.insert(0, str(tools))
    try:
        spec = importlib.util.spec_from_file_location(
            "fetch_articulado", tools / "fetch_articulado.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.path.remove(str(tools))


def test_el_yaml_se_reproduce_desde_la_instantanea(fetcher):
    articulado = sources.load_articulado()
    snapshot = (sources.SOURCES / articulado.snapshot).read_text(encoding="utf-8")

    extracted = fetcher.extract_clauses(snapshot)

    assert extracted == [c.model_dump() for c in articulado.clauses]


def test_la_instantanea_coincide_con_su_hash():
    sources.load_articulado.cache_clear()
    assert sources.load_articulado().clauses


def test_revision_del_articulado_ligada_al_hash():
    """Sin revisión humana todavía el estado es pendiente, no un fallo del gate.

    Cuando exista `sources/review.yaml`, un texto cambiado sin revisión nueva (STALE) o un
    registro ilegible (INVALID) sí falla.
    """
    status = sources.review_status()

    if status.state is ReviewState.UNREVIEWED:
        pytest.skip("pendiente de revisión humana del articulado (sources/review.yaml)")
    assert status.state is ReviewState.CURRENT, status.message


def test_revision_con_hash_distinto_es_obsoleta(tmp_path, monkeypatch):
    (tmp_path / "review.yaml").write_text(
        yaml.safe_dump(
            {
                "articulado_sha256": "0" * 64,
                "reviewed_by": "Ana Ruiz",
                "reviewed_at": "2026-10-20T09:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "articulado.yaml").write_bytes((sources.SOURCES / "articulado.yaml").read_bytes())
    monkeypatch.setattr(sources, "SOURCES", tmp_path)
    sources.articulado_sha256.cache_clear()

    try:
        assert sources.review_status().state is ReviewState.STALE
    finally:
        sources.articulado_sha256.cache_clear()


def block(number, *paragraphs):
    body = "".join(f'<p class="parrafo">{p}</p>' for p in paragraphs)
    heading = f'<h5 class="articulo">Artículo {number}. Título.</h5>'
    return f'<div class="bloque" id="a{number}">{heading}{body}</div>'


def clauses_of(fetcher, *paragraphs, apartado="2"):
    snapshot = block(2, *paragraphs)
    selection = (("x", "ref", "art2", apartado, None),)
    original = fetcher.SELECTION
    fetcher.SELECTION = selection
    try:
        return fetcher.extract_clauses(snapshot)
    finally:
        fetcher.SELECTION = original


def test_el_apartado_termina_en_el_siguiente_esperado(fetcher):
    (clause,) = clauses_of(fetcher, "1. Uno.", "2. Dos.", "Continúa.", "3. Tres.")

    assert clause["text"] == "2. Dos.\n\nContinúa."


def test_el_ultimo_apartado_llega_al_final_del_bloque(fetcher):
    (clause,) = clauses_of(fetcher, "1. Uno.", "2. Dos.", "Continúa.")

    assert clause["text"] == "2. Dos.\n\nContinúa."


def test_otro_apartado_numerado_antes_del_esperado_aborta(fetcher):
    with pytest.raises(SystemExit, match="aparece"):
        clauses_of(fetcher, "1. Uno.", "2. Dos.", "5. Salto.", "3. Tres.")


def test_un_apartado_inexistente_aborta(fetcher):
    with pytest.raises(SystemExit, match="el apartado 2 aparece 0 veces"):
        clauses_of(fetcher, "1. Uno.", "3. Tres.")


def test_un_apartado_repetido_aborta(fetcher):
    with pytest.raises(SystemExit, match="el apartado 2 aparece 2 veces"):
        clauses_of(fetcher, "2. Dos.", "2. Otra vez.")


def test_un_bloque_ausente_aborta(fetcher):
    with pytest.raises(SystemExit, match="falta el bloque"):
        fetcher.extract_clauses(block(99, "1. Uno."))


def test_una_letra_duplicada_o_inexistente_aborta(fetcher):
    for paragraphs in (("1. Uno.", "a) A.", "a) Otra."), ("1. Uno.", "b) B.")):
        snapshot = block(12, *paragraphs)
        original = fetcher.SELECTION
        fetcher.SELECTION = (("x", "ref", "art12", "1", "a"),)
        try:
            with pytest.raises(SystemExit, match="única letra a"):
                fetcher.extract_clauses(snapshot)
        finally:
            fetcher.SELECTION = original


def test_un_numero_de_bloques_distinto_del_esperado_aborta(fetcher):
    with pytest.raises(SystemExit, match="se esperaban"):
        fetcher.extract_snapshot(block(1, "x"))
