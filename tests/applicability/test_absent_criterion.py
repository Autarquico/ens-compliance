"""Criterio de `absent_asset_types.yaml` (ADR 0009): una medida solo entra si su base y todos sus
refuerzos tratan exclusivamente del tipo de activo nombrado.

La justificación de cada entrada está escrita a mano y se compara con el texto del catálogo:
un fragmento por cláusula (o uno para toda la medida, tomado del título, el preámbulo o la
introducción de la ficha). Si el catálogo cambia y el fragmento desaparece, el test falla.
"""

import hashlib
import json
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


# sha256 del texto de preámbulo, base y refuerzos de cada ficha tal como se revisó con el
# criterio estricto. Si el catálogo cambia una cláusula, hay que volver a revisar la entrada.
REVIEWED_CARD_SHA256 = {
    "mp.eq.3": "08695b4bd291d371e418aca0fb36ff8faef7cc2195b0a259ff1577c5b69d8953",
    "mp.eq.4": "c7ca7bda8520202cb874c563e24f16a2e515b690ec12a0cf799b9b65c7055da4",
    "mp.info.1": "26153b24be57c403caabd2722553e41ba779076247bc50461a40a5fbc40c5201",
    "mp.s.1": "9616b4256cdd6f08a81c8393c2fb1d56c3743b8f5ea9ff2142f9b41e6f043637",
    "mp.s.2": "fa10cbb54d41a644060a1a383c8c92b8ad2c93b4d1b789818cf8cd1ef0f60a2f",
    "op.acc.5": "89b0a0c4a90f5f85aff0b2066486f923e4c9c9f7e38e29bd89d4615c7f065766",
    "op.ext.1": "f399a729e40557073f2e48e4a7f6789d35a7195e256cd5e919817fac60ecbbc1",
    "op.ext.2": "38d3be67d5fd62c6dec5977436684cd35b6011ddb0a90225ae7830388baf22e1",
    "op.ext.4": "0b46665d1f38a9240a06cbb04b35cd92763f057c8a7d7b59ebd286cdad536032",
    "op.nub.1": "3c183f4b7b9f528adc6e421a38adb7c19feac9b2fd942acba78d9167ee3a92bd",
}


def reviewed_text(card) -> str:
    parts = [("preamble", text) for text in card.preamble or []]
    parts += [(clause.id or "", clause.text) for clause in card.base]
    parts += [(clause.id or "", clause.text) for r in card.reinforcements for clause in r.clauses]
    return json.dumps(parts, ensure_ascii=False, sort_keys=True)


@pytest.mark.parametrize("measure_id", sorted(JUSTIFIED))
def test_la_ficha_es_la_que_se_reviso(measure_id):
    card = load_catalog().get(measure_id)
    digest = hashlib.sha256(reviewed_text(card).encode()).hexdigest()

    assert digest == REVIEWED_CARD_SHA256[measure_id], (
        f"{measure_id}: la ficha del catálogo cambió; revisa la entrada con el criterio estricto"
    )


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
