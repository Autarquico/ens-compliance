import pytest

from ens_evidence.catalog.model import Dimension, SecurityLevel
from ens_evidence.categorization import CategorizationFile, commitment, compute
from ens_evidence.models.enums import Category

from .helpers import all_dimensions, information, ratings, service


def categorization(services=(), infos=()) -> CategorizationFile:
    return CategorizationFile.model_validate(
        {"information": list(infos), "services": list(services)}
    )


# (C, I, T, A, D) -> (niveles por dimensión, categoría): anexo I, 4.1, escritos a mano.
ANNEX_I_CASES = [
    pytest.param(("BAJO", "BAJO", "BAJO", "BAJO", "BAJO"), "BAJO", Category.BASICA, id="todo-bajo"),
    pytest.param(("MEDIO", "BAJO", "BAJO", "BAJO", "BAJO"), "MEDIO", Category.MEDIA, id="un-medio"),
    pytest.param(
        ("BAJO", "BAJO", "BAJO", "BAJO", "ALTO"), "ALTO", Category.ALTA, id="alta-con-solo-d-alto"
    ),
    pytest.param(
        ("ALTO", "BAJO", "BAJO", "BAJO", "BAJO"), "ALTO", Category.ALTA, id="alta-con-solo-c-alto"
    ),
    pytest.param(
        ("MEDIO", "MEDIO", "MEDIO", "MEDIO", "MEDIO"), "MEDIO", Category.MEDIA, id="todo-medio"
    ),
    pytest.param(
        ("BAJO", "MEDIO", "ALTO", "MEDIO", "BAJO"), "ALTO", Category.ALTA, id="mezcla-con-alto"
    ),
    pytest.param(
        ("not_affected", "BAJO", "not_affected", "not_affected", "not_affected"),
        "BAJO",
        Category.BASICA,
        id="not_affected-no-sube-la-categoria",
    ),
    pytest.param(
        ("not_affected", None, "MEDIO", None, "not_affected"),
        "MEDIO",
        Category.MEDIA,
        id="mezcla-de-none-y-not_affected",
    ),
    pytest.param(("not_affected",) * 5, None, None, id="todo-not_affected-es-indeterminada"),
    pytest.param((None,) * 5, None, None, id="sin-valorar-es-indeterminada"),
]


@pytest.mark.parametrize(("options", "highest", "category"), ANNEX_I_CASES)
def test_el_nivel_de_cada_dimension_es_el_valorado_y_la_categoria_es_la_del_maximo(
    options, highest, category
):
    rated = ratings(**dict(zip((d.value for d in Dimension), options, strict=True)))

    result = compute(categorization([service("svc", rated)]))

    expected = [SecurityLevel(o) if o in ("BAJO", "MEDIO", "ALTO") else None for o in options]
    assert [item.level for item in result.levels] == expected
    assert result.category is category


def test_la_categoria_es_la_del_maximo_entre_varios_elementos_aunque_cada_uno_sea_menor():
    result = compute(
        categorization(
            [
                service("svc-a", ratings(C="BAJO", I="BAJO", T="BAJO", A="BAJO", D="BAJO")),
                service("svc-b", ratings(C="MEDIO", D="BAJO")),
            ],
            [information("inf-a", ratings(I="ALTO"))],
        )
    )

    assert [item.level for item in result.levels] == [
        SecurityLevel.MEDIO,
        SecurityLevel.ALTO,
        SecurityLevel.BAJO,
        SecurityLevel.BAJO,
        SecurityLevel.BAJO,
    ]
    assert result.category is Category.ALTA


@pytest.mark.parametrize(
    ("level", "category"),
    [("ALTO", Category.ALTA), ("MEDIO", Category.MEDIA), ("BAJO", Category.BASICA)],
)
def test_un_unico_nivel_en_una_dimension_fija_la_categoria(level, category):
    result = compute(categorization([service("svc", ratings(D=level))]))

    assert result.category is category


def test_un_nivel_alto_en_cualquier_dimension_hace_la_categoria_alta():
    rated = ratings(C="BAJO", I="MEDIO", T="BAJO", A="ALTO", D="BAJO")

    assert compute(categorization([service("svc", rated)])).category is Category.ALTA


def test_sin_niveles_la_categoria_es_indeterminada():
    result = compute(
        categorization([service("svc", {})], [information("i", ratings(C="not_affected"))])
    )

    assert result.category is None
    assert all(item.level is None for item in result.levels)


def test_el_nivel_del_sistema_es_el_mayor_entre_informaciones_y_servicios():
    result = compute(
        categorization(
            [service("svc-a", ratings(C="BAJO", I="MEDIO")), service("svc-b", ratings(C="MEDIO"))],
            [
                information("inf-a", ratings(C="BAJO", I="ALTO")),
                information("inf-b", ratings(T="BAJO")),
            ],
        )
    )

    levels = {item.dimension: item for item in result.levels}
    assert levels[Dimension.C].level is SecurityLevel.MEDIO
    assert levels[Dimension.C].determined_by == ("service/svc-b",)
    assert levels[Dimension.I].level is SecurityLevel.ALTO
    assert levels[Dimension.I].determined_by == ("information/inf-a",)
    assert levels[Dimension.T].level is SecurityLevel.BAJO
    assert levels[Dimension.A].level is None
    assert result.category is Category.ALTA


def test_empatados_en_el_maximo_todos_figuran_como_determinantes():
    result = compute(
        categorization([service("svc-a", ratings(C="MEDIO")), service("svc-b", ratings(C="MEDIO"))])
    )

    assert result.levels[0].determined_by == ("service/svc-a", "service/svc-b")


def test_not_affected_no_baja_ni_sube_el_nivel_de_otro_elemento():
    result = compute(
        categorization(
            [service("svc-a", ratings(C="not_affected")), service("svc-b", ratings(C="BAJO"))]
        )
    )

    assert result.levels[0].level is SecurityLevel.BAJO
    assert result.levels[0].determined_by == ("service/svc-b",)


@pytest.mark.parametrize(
    ("category", "expected"),
    [
        (Category.BASICA, "autoevaluación"),
        (Category.MEDIA, "preparado para certificación"),
        (Category.ALTA, "preparado para certificación"),
    ],
)
def test_la_categoria_decide_el_compromiso(category, expected):
    assert commitment(category) == expected


def test_ninguna_categoria_compromete_conformidad_declarada():
    forbidden = {"READY", "COMPLIANT", "DECLARED"}

    for category in Category:
        assert not forbidden & set(commitment(category).upper().split())
    assert (
        compute(categorization([service("svc", all_dimensions("ALTO"))])).category is Category.ALTA
    )
