"""Propuesta de aplicabilidad: marca lo exigido por el anexo II, sin tocar decisiones humanas."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime

from pydantic import BaseModel, ValidationError

from ens_evidence.applicability.context import Context, load_context
from ens_evidence.applicability.selection import is_own_proposal, proposal_text
from ens_evidence.applicability.validate import (
    ApplicabilityRejected,
    _measure_errors,
    load_checked_measures,
)
from ens_evidence.catalog.controls import init_measures, save_measures
from ens_evidence.models.enums import Applicability
from ens_evidence.models.readiness import AbsentAssets, Compensation, Measure, SelectionBasis
from ens_evidence.scope.inventory import load_assets
from ens_evidence.scope.messages import describe_validation
from ens_evidence.scope.sync import MEASURES_KEY
from ens_evidence.workspace import WorkspaceStore

MIN_REASON = 20
GENERIC_REASONS = frozenset({"n/a", "na", "no aplica", "no", "ninguno", "ninguna", "-", "x"})


def _own_reason_errors(applicability: Applicability, justification: str) -> list[str]:
    text = justification.strip()
    if applicability == Applicability.APPLICABLE:
        return []
    if len(text) < MIN_REASON or text.rstrip(".").lower() in GENERIC_REASONS:
        return [
            "Escribe una razón propia: por qué esta medida no tiene sentido en tu sistema "
            f"(al menos {MIN_REASON} caracteres; «no aplica» no basta)."
        ]
    return []


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
            return _confirmed(measure, basis)
        result.pending_review.append(measure.id)
        return measure
    result.unchanged += 1
    return measure


def _confirmed(measure: Measure, basis: SelectionBasis) -> Measure:
    """La decisión humana pasa a ser la vigente: se registra su fundamento."""
    return measure.model_copy(update={"selection": basis, "proposed": False})


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


def decide(
    store: WorkspaceStore,
    now: datetime,
    measure_id: str,
    applicability: Applicability,
    justification: str,
    chosen_reinforcements: Iterable[str] = (),
    compensation: Compensation | Mapping | None = None,
    absent_assets: AbsentAssets | Mapping | None = None,
) -> Measure:
    """Decisión humana de una medida, registrada con el fundamento vigente.

    Crea `controls/measures.yaml` si falta. Lanza `ScopeNotApproved`, `CategorizationNotCurrent`
    o `ApplicabilityRejected` con lo que `applicability validate` rechazaría para esa medida;
    en ese caso no escribe nada. Conserva la valoración, la revisión y las evidencias.
    """
    if errors := _own_reason_errors(applicability, justification):
        raise ApplicabilityRejected(errors)
    context = load_context(store, now)
    if not store.exists(MEASURES_KEY):
        init_measures(store)
    document = load_checked_measures(store, context)
    current = next((m for m in document.measures if m.id == measure_id), None)
    if current is None:
        raise ApplicabilityRejected([f"{MEASURES_KEY}: {measure_id} no es una medida del catálogo"])
    fields = {
        "applicability": applicability,
        "justification": justification,
        "chosen_reinforcements": list(chosen_reinforcements),
        "compensation": _plain(compensation),
        "absent_assets": _plain(absent_assets),
        "proposed": False,
    }
    raw = {**current.model_dump(mode="json", exclude_none=True), **fields}
    try:
        decided = _confirmed(Measure.model_validate(raw), context.basis(measure_id))
    except ValidationError as exc:
        raise ApplicabilityRejected(describe_validation(MEASURES_KEY, exc, raw)) from None
    assets_file, _ = load_assets(store)
    assets = assets_file.assets if assets_file else []
    if errors := _measure_errors(decided, context, assets):
        raise ApplicabilityRejected(errors)
    updated = [decided if m.id == measure_id else m for m in document.measures]
    save_measures(store, document.model_copy(update={"measures": updated}))
    return decided


def _plain(value: object) -> object:
    return value.model_dump(mode="json") if isinstance(value, BaseModel) else value
