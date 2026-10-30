"""Propuesta de aplicabilidad: marca lo exigido por el anexo II, sin tocar decisiones humanas."""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

from ens_evidence.applicability.context import Context, load_context
from ens_evidence.applicability.selection import is_own_proposal, proposal_text
from ens_evidence.applicability.validate import load_checked_measures
from ens_evidence.catalog.controls import save_measures
from ens_evidence.models.enums import Applicability
from ens_evidence.models.readiness import Measure, SelectionBasis
from ens_evidence.workspace import WorkspaceStore


@dataclass
class ProposalResult:
    proposed: list[str] = field(default_factory=list)
    refreshed: list[str] = field(default_factory=list)
    retracted: list[str] = field(default_factory=list)
    pending_review: list[str] = field(default_factory=list)
    confirmed: list[str] = field(default_factory=list)
    unconfirmed: list[str] = field(default_factory=list)
    unchanged: int = 0


def _propose_one(
    measure: Measure, context: Context, confirm: frozenset[str], result: ProposalResult
) -> Measure:
    selection = context.selections[measure.id]
    basis = context.basis(measure.id)
    if is_own_proposal(measure):
        if not selection.required:
            result.retracted.append(measure.id)
            return measure.model_copy(
                update={
                    "applicability": Applicability.UNDETERMINED,
                    "justification": None,
                    "selection": None,
                    "proposed": False,
                    "chosen_reinforcements": [],
                }
            )
        if measure.selection != basis:
            result.refreshed.append(measure.id)
            return _proposed(measure, basis)
    elif measure.applicability is Applicability.UNDETERMINED:
        if selection.required:
            result.proposed.append(measure.id)
            return _proposed(measure, basis)
    elif measure.selection != basis:
        if measure.id in confirm:
            result.confirmed.append(measure.id)
            return measure.model_copy(update={"selection": basis, "proposed": False})
        result.pending_review.append(measure.id)
        return measure
    result.unchanged += 1
    return measure


def _proposed(measure: Measure, basis: SelectionBasis) -> Measure:
    return measure.model_copy(
        update={
            "applicability": Applicability.APPLICABLE,
            "justification": proposal_text(basis),
            "selection": basis,
            "proposed": True,
            "chosen_reinforcements": [],
        }
    )


def propose_applicability(
    store: WorkspaceStore, now: datetime, confirm: Iterable[str] = ()
) -> ProposalResult:
    """Lanza `ScopeNotApproved`, `CategorizationNotCurrent` o `ApplicabilityRejected`.

    Nunca marca NOT_APPLICABLE, nunca decide por una persona y es idempotente. Una decisión
    humana con fundamento ausente o distinto queda pendiente hasta que se pasa en `confirm`.
    """
    context = load_context(store, now)
    document = load_checked_measures(store, context)
    result = ProposalResult()
    confirmed = frozenset(confirm)
    updated = [_propose_one(m, context, confirmed, result) for m in document.measures]
    result.unconfirmed = sorted(confirmed - set(result.confirmed))
    if updated != document.measures:
        save_measures(store, document.model_copy(update={"measures": updated}))
    return result
