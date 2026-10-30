"""Qué medidas exige el anexo II para una categorización. Funciones puras."""

from dataclasses import dataclass

from ens_evidence.catalog.model import BASE, Axis, MeasureCard
from ens_evidence.categorization import LEVEL_ORDER, Computation
from ens_evidence.models.enums import Category
from ens_evidence.models.readiness import Measure, SelectionBasis

PROPOSAL_PREFIX = "exigida por el anexo II para "


@dataclass(frozen=True)
class Selection:
    measure_id: str
    required: bool
    required_by: str | None = None
    reinforcements: tuple[str, ...] = ()
    alternatives: tuple[tuple[str, ...], ...] = ()


def select(card: MeasureCard, computation: Computation) -> Selection:
    """Exigencia de la medida: `category` mira solo la categoría; `dimension`, el mayor nivel
    entre las dimensiones de la ficha (no acumula niveles)."""
    if card.applies_by is Axis.CATEGORY:
        assert computation.category is not None
        return _at_level(card, computation.category.value, f"la categoría {computation.category}")
    affected = [
        (item.dimension, item.level)
        for item in computation.levels
        if item.level is not None and item.dimension in card.dimensions
    ]
    if not affected:
        return Selection(card.id, required=False)
    highest = max((level for _, level in affected), key=LEVEL_ORDER.__getitem__)
    dimensions = tuple(d for d, level in affected if level is highest)
    names = ", ".join(d.value for d in dimensions)
    noun = "la dimensión" if len(dimensions) == 1 else "las dimensiones"
    return _at_level(card, highest.value, f"{noun} {names} en nivel {highest.value}")


def _at_level(card: MeasureCard, level: str, reason: str) -> Selection:
    requirement = card.levels[level]
    if requirement.not_applicable:
        return Selection(card.id, required=False)
    return Selection(
        card.id,
        required=True,
        required_by=reason,
        reinforcements=tuple(ref for ref in requirement.all_of if ref != BASE),
        alternatives=tuple(tuple(group) for group in requirement.any_of),
    )


def basis_of(
    selection: Selection, category: Category, catalog_snapshot_sha256: str
) -> SelectionBasis:
    return SelectionBasis(
        category=category,
        catalog_snapshot_sha256=catalog_snapshot_sha256,
        required_by=selection.required_by,
        required_reinforcements=list(selection.reinforcements),
        alternatives=[list(group) for group in selection.alternatives],
    )


def proposal_text(basis: SelectionBasis) -> str:
    """Justificación que escribe `propose`; también sirve para reconocer lo que escribió."""
    text = f"{PROPOSAL_PREFIX}{basis.required_by}"
    if basis.required_reinforcements:
        text += f"; refuerzos exigidos: {', '.join(basis.required_reinforcements)}"
    for group in basis.alternatives:
        text += f"; refuerzo alternativo (uno de): {', '.join(group)}"
    return text


def is_own_proposal(measure: Measure) -> bool:
    """La decisión la escribió `propose` y nadie la ha tocado: aún puede reescribirla."""
    return (
        measure.proposed
        and measure.review is None
        and measure.selection is not None
        and measure.justification == proposal_text(measure.selection)
    )
