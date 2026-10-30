"""Criterio de `absent_asset_types.yaml` (ADR 0009): una medida solo entra si su base y todos sus
refuerzos tratan exclusivamente del tipo de activo nombrado.

La justificación de cada entrada está escrita a mano y se compara con el texto del catálogo:
un fragmento por cláusula (o uno para toda la medida, tomado del título, el preámbulo o la
introducción de la ficha). Si el catálogo cambia y el fragmento desaparece, el test falla.
"""

import re

import pytest

from ens_evidence.applicability.absent import absent_asset_types
from ens_evidence.catalog import load_catalog

LOCALES = "Los locales donde se ubiquen los sistemas de información"

# id -> (tipo de activo, alcance de toda la medida, cláusulas con alcance propio).
# Alcance: (origen, fragmento) con origen "name" | "preamble" | "intro" (sin id).
# Cláusula: id -> (origen, fragmento); el origen es "self" o la cláusula cuyo texto la ancla.
JUSTIFIED: dict[str, tuple[str, list[tuple[str, str]], dict[str, tuple[str, str]]]] = {
    "op.acc.5": (
        "usuarios externos",
        [("name", "usuarios externos"), ("preamble", "no son usuarios de la organización")],
        {},
    ),
    "op.ext.1": (
        "servicios prestados por terceros",
        [],
        {"op.ext.1.1": ("self", "responsabilidad del prestador")},
    ),
    "op.ext.2": (
        "servicios prestados por terceros",
        [],
        {
            "op.ext.2.1": ("self", "obligaciones de servicio"),
            "op.ext.2.2": ("self", "comprendidos en el acuerdo"),
        },
    ),
    "op.ext.4": (
        "interconexión con otros sistemas",
        [("preamble", "enlaces con otros sistemas de información")],
        {},
    ),
    "op.nub.1": (
        "servicios en la nube",
        [("name", "servicios en la nube")],
        {},
    ),
    "mp.eq.3": (
        "dispositivos portátiles",
        [("name", "dispositivos portátiles"), ("intro", "ordenadores portátiles, tabletas")],
        {},
    ),
    "mp.eq.4": (
        "otros dispositivos conectados a la red",
        [
            ("name", "dispositivos conectados a la red"),
            ("preamble", "dispositivos conectados a la red"),
        ],
        {},
    ),
    "mp.info.1": (
        "datos personales",
        [("name", "Datos personales")],
        {"mp.info.1.1": ("self", "Cuando el sistema trate datos personales")},
    ),
    "mp.s.1": (
        "correo electrónico",
        [("name", "correo electrónico"), ("intro", "El correo electrónico se protegerá")],
        {},
    ),
    "mp.s.2": (
        "servicios y aplicaciones web",
        [("name", "servicios y aplicaciones web"), ("intro", "servicios web")],
        {},
    ),
}

NOT_TIED_TO_THE_ASSET = {
    "op.ext.3": "op.ext.3.r3.1: componentes de programas informáticos y productos suministrados",
    "mp.if.1": "mp.if.1.1: equipamiento del CPD «en la medida de lo posible»; mp.if.1.2: áreas",
    "mp.if.2": "mp.if.2.1: locales donde hay equipamiento esencial, no solo instalaciones propias",
    "mp.if.3": "intro: locales donde se ubiquen los sistemas, existan o no instalaciones propias",
    "mp.if.4": "mp.if.4.1: locales donde se ubiquen los sistemas y sus componentes esenciales",
    "mp.if.5": "mp.if.5.1: locales donde se ubiquen los sistemas y sus componentes esenciales",
    "mp.if.6": "mp.if.6.1: locales donde se ubiquen los sistemas y sus componentes esenciales",
    "mp.if.7": "mp.if.7.1: equipamiento esencial, con o sin instalaciones propias",
    "mp.sw.1": "mp.sw.1.r4.1: pruebas previas a implantar o modificar cualquier sistema",
    "mp.info.4": "mp.info.4.4: sellos cualificados, sin atarlos a la evidencia electrónica",
}


def squash(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def texts_of(card) -> dict[str, str]:
    """Texto de cada origen posible: título, preámbulo, introducción y cláusulas con id."""
    clauses = [c for c in card.base] + [c for r in card.reinforcements for c in r.clauses]
    texts = {
        "name": card.name,
        "preamble": " ".join(card.preamble),
        "intro": " ".join(c.text for c in card.base if c.id is None),
    }
    texts.update({c.id: c.text for c in clauses if c.id})
    return {key: squash(value) for key, value in texts.items()}


def clause_ids(card) -> list[str]:
    clauses = [c for c in card.base] + [c for r in card.reinforcements for c in r.clauses]
    return [c.id for c in clauses if c.id]


def test_la_tabla_cubre_exactamente_la_lista():
    assert set(JUSTIFIED) == set(absent_asset_types())


@pytest.mark.parametrize("measure_id", sorted(JUSTIFIED))
def test_el_tipo_de_activo_de_la_tabla_es_el_de_la_lista(measure_id):
    assert absent_asset_types()[measure_id] == (JUSTIFIED[measure_id][0],)


@pytest.mark.parametrize("measure_id", sorted(JUSTIFIED))
def test_cada_fragmento_justificativo_esta_en_el_catalogo(measure_id):
    card = load_catalog().get(measure_id)
    texts = texts_of(card)
    _, scope, own = JUSTIFIED[measure_id]

    for source, fragment in scope:
        assert squash(fragment) in texts[source], f"{measure_id}: «{fragment}» no está en {source}"
    for clause_id, (source, fragment) in own.items():
        where = clause_id if source == "self" else source
        assert squash(fragment) in texts[where], f"{measure_id}: «{fragment}» no está en {where}"


@pytest.mark.parametrize("measure_id", sorted(JUSTIFIED))
def test_toda_clausula_de_base_y_refuerzos_queda_atada_al_tipo_de_activo(measure_id):
    card = load_catalog().get(measure_id)
    _, scope, own = JUSTIFIED[measure_id]

    uncovered = [] if scope else [c for c in clause_ids(card) if c not in own]
    unknown = [c for c in own if c not in clause_ids(card)]

    assert uncovered == [], f"{measure_id}: sin justificar {uncovered}"
    assert unknown == []


@pytest.mark.parametrize("measure_id", sorted(NOT_TIED_TO_THE_ASSET))
def test_las_medidas_retiradas_no_estan_en_la_lista(measure_id):
    assert measure_id not in absent_asset_types()
    assert load_catalog().get(measure_id) is not None
