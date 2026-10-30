"""Huellas de la matriz: los enlaces de una entrada y el contexto de una medida.

`measure_context_sha256` fija a qué medida, tal como la firmó la Declaración, se refiere una
revisión. No incluye la huella del alcance: dar de alta un activo no obliga a revisar todas las
medidas (ADR 0011); la Declaración se vuelve a firmar cuando cambia el alcance.
"""

import hashlib
import json

from ens_evidence.applicability import Declaration
from ens_evidence.catalog import Catalog, load_catalog
from ens_evidence.catalog.model import BASE, MeasureCard
from ens_evidence.matrix.model import MatrixEntry


def _digest(payload: object) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def links_sha256(entry: MatrixEntry) -> str:
    """Enlaces y `covers` de la entrada; no el orden, ni responsable, periodicidad o notas."""
    links = sorted(
        ([*link.target, sorted(link.covers)] for link in entry.links),
        key=lambda item: (item[0], item[1]),
    )
    return _digest(links)


def _card_text(card: MeasureCard, obligation_ids: list[str]) -> str:
    """Texto de la ficha de las obligaciones en juego."""
    parts = {}
    reinforcements = {r.id: r.clauses for r in card.reinforcements}
    for obligation_id in obligation_ids:
        clauses = card.base if obligation_id == BASE else reinforcements.get(obligation_id)
        if clauses is None:
            continue
        parts[obligation_id] = [[c.id, c.label, c.text] for c in clauses]
    return _digest(parts)


def _declared(declaration: Declaration, measure_id: str) -> dict:
    for selected in declaration.selected:
        if selected.id == measure_id:
            return {
                "kind": "selected",
                "required_by": selected.required_by,
                "required": sorted(selected.required_reinforcements),
                "alternatives": sorted(sorted(group) for group in selected.alternatives),
                "chosen": sorted(selected.chosen_reinforcements),
            }
    for not_selected in declaration.not_selected:
        if not_selected.id == measure_id:
            return {"kind": "not_selected", "justification": not_selected.justification}
    for absent in declaration.required_but_absent_assets:
        if absent.id == measure_id:
            return {
                "kind": "absent_assets",
                "required_by": absent.required_by,
                "asset_type": absent.asset_type,
                "justification": absent.justification,
            }
    raise LookupError(f"{measure_id} no está en la Declaración")


def measure_context_sha256(
    declaration: Declaration, measure_id: str, catalog: Catalog | None = None
) -> str:
    """Huella de lo que la Declaración dice de la medida y del texto de su ficha."""
    declared = _declared(declaration, measure_id)
    compensation = next((c for c in declaration.compensations if c.replaces == measure_id), None)
    card = (catalog or load_catalog()).get(measure_id)
    in_play = (
        sorted({BASE, *declared["required"], *declared["chosen"]})
        if declared["kind"] == "selected"
        else [BASE]
    )
    if compensation:
        in_play = sorted({*in_play, *compensation.obligations})
    return _digest(
        {
            "declared": declared,
            "compensation": compensation.model_dump(mode="json") if compensation else None,
            "card_text": _card_text(card, in_play) if card else None,
        }
    )
