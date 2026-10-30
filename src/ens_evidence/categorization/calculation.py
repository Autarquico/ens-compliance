"""Nivel del sistema por dimensión y categoría (anexo I, 3 y 4.1). Funciones puras."""

from dataclasses import dataclass

from ens_evidence.catalog.model import Dimension, SecurityLevel
from ens_evidence.categorization.models import CategorizationFile
from ens_evidence.models.enums import Category

LEVEL_ORDER = {SecurityLevel.BAJO: 1, SecurityLevel.MEDIO: 2, SecurityLevel.ALTO: 3}
_CATEGORY = {
    SecurityLevel.ALTO: Category.ALTA,
    SecurityLevel.MEDIO: Category.MEDIA,
    SecurityLevel.BAJO: Category.BASICA,
}
_COMMITMENT = {
    Category.BASICA: "autoevaluación",
    Category.MEDIA: "preparado para certificación",
    Category.ALTA: "preparado para certificación",
}
_NOTICE = {
    Category.BASICA: "La autoevaluación la hace la organización; la herramienta no declara.",
    Category.MEDIA: "Requiere auditoría de certificación externa; la herramienta no certifica.",
    Category.ALTA: "Requiere auditoría de certificación externa; la herramienta no certifica.",
}


@dataclass(frozen=True)
class DimensionLevel:
    dimension: Dimension
    level: SecurityLevel | None
    determined_by: tuple[str, ...] = ()


@dataclass(frozen=True)
class Computation:
    levels: tuple[DimensionLevel, ...]
    category: Category | None


def compute(categorization: CategorizationFile) -> Computation:
    """Máximo por dimensión entre informaciones y servicios; `determined_by` dice quién lo fija."""
    elements = [(f"information/{item.id}", item.ratings) for item in categorization.information] + [
        (f"service/{item.id}", item.ratings) for item in categorization.services
    ]
    levels = tuple(_dimension_level(dimension, elements) for dimension in Dimension)
    return Computation(levels, _category(levels))


def _dimension_level(dimension: Dimension, elements: list) -> DimensionLevel:
    rated = [
        (ref, rating.security_level)
        for ref, ratings in elements
        if (rating := ratings.get(dimension)) is not None and rating.security_level is not None
    ]
    if not rated:
        return DimensionLevel(dimension, None)
    highest = max((level for _, level in rated), key=LEVEL_ORDER.__getitem__)
    return DimensionLevel(
        dimension, highest, tuple(ref for ref, level in rated if level is highest)
    )


def _category(levels: tuple[DimensionLevel, ...]) -> Category | None:
    present = [item.level for item in levels if item.level is not None]
    if not present:
        return None
    return _CATEGORY[max(present, key=LEVEL_ORDER.__getitem__)]


def commitment(category: Category) -> str:
    """Resultado comprometido según la categoría (docs/vision.md); nunca declara conformidad."""
    return _COMMITMENT[category]


def commitment_notice(category: Category) -> str:
    """Aviso que acompaña siempre al compromiso (docs/specs/assessment-dossier.md)."""
    return _NOTICE[category]
