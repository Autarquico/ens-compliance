import pytest
import yaml

from ens_evidence.catalog import catalog_sha256, load_catalog
from ens_evidence.orgdocs import DocumentMeta, load_templates
from ens_evidence.orgdocs.sources import articulado_sha256, load_articulado

from .orgdocs_helpers import DOCUMENTS, traceability_rows

REQUIRED_CLAUSES = {
    "politica-seguridad": {
        *(f"org.1.{n}" for n in range(1, 6)),
        "art.12.1.f",
        "art.2.3",
        "art.11.3",
        "art.13.2",
        "art.13.5",
    },
    "designacion-responsables": {"org.1.3", "art.13.2", "art.11.3", "art.13.3"},
    "normativa-seguridad": {"org.2.1", "org.2.2", "art.15.2"},
    "procedimientos-seguridad": {f"org.3.{n}" for n in range(1, 5)},
    "proceso-autorizacion": {f"org.4.{n}" for n in range(1, 9)},
}


def read_meta(store, name) -> DocumentMeta:
    return DocumentMeta.model_validate(
        yaml.safe_load(store.read_text(f"org/docs/{name}.meta.yaml"))
    )


def catalog_text(clause_id: str) -> str:
    """Texto del anexo II tomado del catálogo empaquetado, sin pasar por el índice del motor."""
    measure_id, _, tail = clause_id.rpartition(".")
    for measure in load_catalog().measures:
        clauses = [c for r in measure.reinforcements for c in r.clauses] + list(measure.base)
        if tail == "intro" and measure.id == measure_id:
            return next(c.text for c in measure.base if c.id is None)
        for clause in clauses:
            if clause.id == clause_id:
                return clause.text
    raise AssertionError(clause_id)


def articulado_text(clause_id: str) -> str:
    return next(c.text for c in load_articulado().clauses if c.id == clause_id)


@pytest.mark.parametrize("name", DOCUMENTS)
def test_cada_documento_lista_las_clausulas_que_soporta(built, name):
    covered = {c.id for c in read_meta(built, name).clauses}

    assert REQUIRED_CLAUSES[name] <= covered


def test_las_clausulas_org_1_a_4_y_los_apartados_del_articulado_citados_estan_cubiertos(built):
    covered = {c.id for n in DOCUMENTS for c in read_meta(built, n).clauses}

    assert set().union(*REQUIRED_CLAUSES.values()) <= covered


@pytest.mark.parametrize("name", DOCUMENTS)
def test_cada_documento_muestra_sus_clausulas_con_su_seccion(built, name):
    document = built.read_text(f"org/docs/{name}.md")
    meta = read_meta(built, name)
    titles = {
        s.id: s.title for s in next(t for t in load_templates() if t.id == name).template.sections
    }

    rows = traceability_rows(document)

    assert [r[2] for r in rows] == [titles[c.section] for c in meta.clauses]


@pytest.mark.parametrize("name", DOCUMENTS)
def test_el_texto_de_cada_clausula_es_el_del_catalogo_o_el_del_articulado(built, name):
    document = built.read_text(f"org/docs/{name}.md")
    meta = read_meta(built, name)

    rows = traceability_rows(document)

    for clause, (_, text, _) in zip(meta.clauses, rows, strict=True):
        official = (
            catalog_text(clause.id) if clause.origin == "catalog" else articulado_text(clause.id)
        )
        assert text == " ".join(official.split()), clause.id


@pytest.mark.parametrize("name", DOCUMENTS)
def test_cada_documento_declara_la_version_de_plantilla_catalogo_y_fuente(built, name):
    document = built.read_text(f"org/docs/{name}.md")
    meta = read_meta(built, name)

    assert f"Plantilla `{name}` v1" in document
    assert catalog_sha256()[:12] in document
    assert articulado_sha256()[:12] in document
    assert (meta.template.id, meta.template.version) == (name, 1)
    assert meta.catalog_sha256 == catalog_sha256()
    assert meta.articulado_sha256 == articulado_sha256()
    assert meta.catalog_consolidation_date.isoformat() in document
    assert meta.articulado_consolidation_date.isoformat() in document


@pytest.mark.parametrize("name", DOCUMENTS)
def test_cada_documento_lista_las_medidas_del_anexo_ii_que_soporta(built, name):
    document = built.read_text(f"org/docs/{name}.md")
    meta = read_meta(built, name)

    assert meta.measures
    for measure in meta.measures:
        assert measure.id in document
        assert measure.covered_clauses
    assert "no acredita que la medida esté implantada" in document


def sections_covering(name, clause):
    loaded = next(t for t in load_templates() if t.id == name)
    return {s.id for s in loaded.template.sections if clause in s.covers}


@pytest.mark.parametrize(
    ("name", "clause", "section"),
    [
        ("politica-seguridad", "art.13.2", "roles"),
        ("politica-seguridad", "art.11.3", "roles"),
        ("politica-seguridad", "org.1.3", "roles"),
        ("designacion-responsables", "org.1.3", "roles"),
        ("designacion-responsables", "org.1.3", "procedimiento"),
        ("designacion-responsables", "art.13.2", "roles"),
    ],
)
def test_la_clausula_apunta_a_la_seccion_que_contiene_lo_que_pide(name, clause, section):
    assert section in sections_covering(name, clause)


def test_una_lista_de_nombres_no_cubre_el_deber_de_designar_responsables():
    assert "personas" not in sections_covering("designacion-responsables", "org.1.3")


def test_la_politica_no_titula_atribuciones_una_seccion_que_no_las_contiene():
    loaded = next(t for t in load_templates() if t.id == "politica-seguridad")

    assert "Atribuciones" not in {s.title for s in loaded.template.sections}
