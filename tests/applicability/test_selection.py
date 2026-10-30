import pytest

from ens_evidence.applicability import select
from ens_evidence.catalog import load_catalog
from ens_evidence.catalog.model import Axis
from ens_evidence.categorization import CategorizationFile, compute
from ens_evidence.models.enums import Category

from .helpers import categorization_document, uniform

CATALOG = load_catalog()
CARDS = {card.id: card for card in CATALOG.measures}
RANK = {"BAJO": 1, "MEDIO": 2, "ALTO": 3}
UNIFORM_LEVELS = [("BAJO", Category.BASICA), ("MEDIO", Category.MEDIA), ("ALTO", Category.ALTA)]
MIXED_PROFILES = [
    {"C": "ALTO", "I": "BAJO", "T": "MEDIO", "A": None, "D": "BAJO"},
    {"C": "BAJO", "I": "BAJO", "T": "BAJO", "A": "BAJO", "D": "ALTO"},
    {"C": None, "I": "MEDIO", "T": None, "A": "BAJO", "D": "MEDIO"},
    {"C": "MEDIO", "I": "ALTO", "T": "ALTO", "A": "MEDIO", "D": None},
]


def computation_of(levels):
    document = categorization_document(levels)
    return compute(CategorizationFile.model_validate(document))


def selection_for(measure_id, levels):
    return select(CARDS[measure_id], computation_of(levels))


def required_reinforcements(card, level):
    return {r.id for r in card.reinforcements if level in r.required_at}


def alternative_reinforcements(card, level):
    return {r.id for r in card.reinforcements if level in r.alternative_at}


def determining(selection):
    """Dimensiones y nivel que nombra `required_by`: 'las dimensiones C, I en nivel MEDIO'."""
    text = selection.required_by.removeprefix("las dimensiones ").removeprefix("la dimensión ")
    names, level = text.split(" en nivel ")
    return set(names.split(", ")), level


def flattened(groups):
    return {ref for group in groups for ref in group}


@pytest.mark.parametrize("measure_id", sorted(CARDS))
@pytest.mark.parametrize(("dimension_level", "category"), UNIFORM_LEVELS)
def test_cada_medida_se_selecciona_como_dice_su_ficha_en_cada_categoria(
    measure_id, dimension_level, category
):
    card = CARDS[measure_id]
    level = category.value if card.applies_by is Axis.CATEGORY else dimension_level

    selection = selection_for(measure_id, uniform(dimension_level))

    assert selection.required is (not card.levels[level].not_applicable)
    if selection.required:
        if card.applies_by is Axis.CATEGORY:
            assert selection.required_by == f"la categoría {category.value}"
        else:
            assert determining(selection)[1] == level
        assert set(selection.reinforcements) == required_reinforcements(card, level)
        assert flattened(selection.alternatives) == alternative_reinforcements(card, level)
        assert len(selection.alternatives) == len(card.levels[level].any_of)
        assert not set(selection.reinforcements) & flattened(selection.alternatives)
    else:
        assert not selection.reinforcements
        assert not selection.alternatives


@pytest.mark.parametrize("levels", MIXED_PROFILES)
@pytest.mark.parametrize(
    "measure_id", [m.id for m in CATALOG.measures if m.applies_by is Axis.DIMENSION]
)
def test_una_medida_por_dimension_se_decide_por_el_mayor_nivel_de_las_dimensiones_de_su_ficha(
    measure_id, levels
):
    card = CARDS[measure_id]
    in_card = {d: levels[d.value] for d in card.dimensions if levels[d.value]}

    selection = selection_for(measure_id, levels)

    if not in_card:
        assert not selection.required
        return
    highest = max(in_card.values(), key=RANK.__getitem__)
    assert selection.required is (not card.levels[highest].not_applicable)
    if selection.required:
        assert determining(selection) == (
            {d.value for d, level in in_card.items() if level == highest},
            highest,
        )


@pytest.mark.parametrize(
    "measure_id", [m.id for m in CATALOG.measures if m.applies_by is Axis.CATEGORY]
)
@pytest.mark.parametrize("levels", MIXED_PROFILES)
def test_una_medida_por_categoria_solo_mira_la_categoria(measure_id, levels):
    card = CARDS[measure_id]
    category = {"BAJO": "BASICA", "MEDIO": "MEDIA", "ALTO": "ALTA"}[
        max((v for v in levels.values() if v), key=RANK.__getitem__)
    ]

    selection = selection_for(measure_id, levels)

    assert selection.required is (not card.levels[category].not_applicable)
    if selection.required:
        assert selection.required_by == f"la categoría {category}"


def test_op_acc_5_en_bajo_exige_una_alternativa_entre_r1_y_r4_y_no_r5():
    selection = selection_for("op.acc.5", uniform("BAJO"))

    assert selection.reinforcements == ()
    assert selection.alternatives == (("R1", "R2", "R3", "R4"),)


@pytest.mark.parametrize("level", ["MEDIO", "ALTO"])
def test_op_acc_5_en_medio_o_alto_exige_r5_y_una_alternativa_entre_r2_y_r4(level):
    selection = selection_for("op.acc.5", uniform(level))

    assert selection.reinforcements == ("R5",)
    assert selection.alternatives == (("R2", "R3", "R4"),)


def test_op_acc_5_ignora_la_dimension_disponibilidad_que_no_esta_en_su_ficha():
    levels = {"C": "BAJO", "I": "BAJO", "T": "BAJO", "A": "BAJO", "D": "ALTO"}

    selection = selection_for("op.acc.5", levels)

    assert determining(selection) == ({"C", "I", "T", "A"}, "BAJO")


def test_op_acc_5_no_cuenta_las_dimensiones_no_afectadas_como_determinantes():
    levels = {"C": "MEDIO", "I": "BAJO", "T": None, "A": "MEDIO", "D": "ALTO"}

    selection = selection_for("op.acc.5", levels)

    assert determining(selection) == ({"C", "A"}, "MEDIO")


def test_op_pl_1_en_alta_exige_el_refuerzo_r2_sin_acumular_r1():
    selection = selection_for("op.pl.1", uniform("ALTO"))

    assert selection.required
    assert selection.reinforcements == ("R2",)


def test_categoria_alta_con_disponibilidad_en_bajo_exige_lo_de_alta_y_lo_de_bajo_en_d():
    levels = {"C": "ALTO", "I": "MEDIO", "T": "BAJO", "A": "BAJO", "D": "BAJO"}

    by_category = selection_for("op.pl.1", levels)
    by_availability_low = selection_for("op.cont.1", levels)
    by_availability_basic = selection_for("op.pl.4", levels)

    assert by_category.required_by == "la categoría ALTA"
    assert by_category.reinforcements == ("R2",)
    assert not by_availability_low.required
    assert by_availability_basic.required
    assert determining(by_availability_basic) == ({"D"}, "BAJO")


def test_la_dimension_d_en_alto_activa_op_cont_2_aunque_la_categoria_ya_fuera_alta():
    levels = {"C": "ALTO", "I": "ALTO", "T": "ALTO", "A": "ALTO", "D": "MEDIO"}

    assert not selection_for("op.cont.2", levels).required
    assert selection_for("op.cont.2", {**levels, "D": "ALTO"}).required


def test_sin_ningun_nivel_ninguna_medida_por_dimension_es_exigida():
    levels = {"C": "BAJO", "I": None, "T": None, "A": None, "D": None}

    selection = selection_for("op.cont.1", levels)

    assert not selection.required
