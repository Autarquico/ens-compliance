"""Pasos de aplicabilidad: decidir cada medida y firmar la Declaración de Aplicabilidad."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ens_evidence.applicability import (
    ApplicabilityRejected,
    DeclarationRejected,
    DeclarationState,
    approve_declaration,
    declaration_status,
    propose_applicability,
    validate_applicability,
)
from ens_evidence.applicability.absent import absent_asset_types
from ens_evidence.applicability.context import Context as Applicable
from ens_evidence.applicability.context import load_context
from ens_evidence.applicability.propose import decide
from ens_evidence.applicability.validate import load_checked_measures
from ens_evidence.catalog.controls import MeasuresFile, MeasuresFileError, init_measures
from ens_evidence.catalog.model import BASE
from ens_evidence.categorization import CategorizationNotCurrent
from ens_evidence.models.enums import Applicability
from ens_evidence.models.readiness import Measure
from ens_evidence.scope import ScopeNotApproved
from ens_evidence.ui.forms import first
from ens_evidence.ui.store import UiStore
from ens_evidence.ui.views import format_instant
from ens_evidence.ui.wizard.base import Context, Step, StepInput, StepRejected, declared_name
from ens_evidence.ui.wizard.risk import (
    CATEGORIZATION_NOT_CURRENT,
    MAX_LISTED_ERRORS,
    SCOPE_NOT_APPROVED,
    plain_errors,
    plain_rejection,
)

APPLIES, NOT_APPLICABLE, ABSENT = "applies", "not_applicable", "absent"
STATE_REASONS = {
    "undecided": "Sin decidir",
    "reinforcement": "Falta elegir un refuerzo",
    "review": "Pendiente de revisión: cambió la categoría o el catálogo desde que la decidiste",
    "unregistered": "Decisión sin confirmar",
}
PENDING_STATES = frozenset(STATE_REASONS)
NEEDS_PREPARING = (
    "Las medidas aún no están preparadas para esta versión del catálogo. "
    "Pulsa «Preparar las medidas»."
)


@dataclass(frozen=True)
class Loaded:
    """Lo que hay que saber para pintar el paso; `blocked` explica por qué no se puede decidir."""

    context: Applicable | None = None
    document: MeasuresFile | None = None
    blocked: str | None = None


def _load(store: UiStore, now: datetime) -> Loaded:
    try:
        context = load_context(store, now)
    except ScopeNotApproved:
        return Loaded(blocked=SCOPE_NOT_APPROVED)
    except CategorizationNotCurrent:
        return Loaded(blocked=CATEGORIZATION_NOT_CURRENT)
    try:
        return Loaded(context, load_checked_measures(store, context))
    except (ApplicabilityRejected, MeasuresFileError):
        return Loaded(context)


def _uncovered_groups(measure: Measure, context: Applicable) -> list[tuple[str, ...]]:
    selection = context.selections[measure.id]
    replaced = set(measure.compensation.replaces) if measure.compensation else set()
    return [g for g in selection.alternatives if replaced.isdisjoint(g)]


def _state(measure: Measure, context: Applicable) -> str:
    if measure.applicability is Applicability.UNDETERMINED:
        return "undecided"
    if measure.applicability is Applicability.APPLICABLE and any(
        len([r for r in measure.chosen_reinforcements if r in group]) != 1
        for group in _uncovered_groups(measure, context)
    ):
        return "reinforcement"
    if measure.selection is None:
        return "unregistered"
    if measure.selection != context.basis(measure.id):
        return "review"
    return "decided"


def _choice(measure: Measure) -> str:
    if measure.absent_assets:
        return ABSENT
    return {
        Applicability.APPLICABLE: APPLIES,
        Applicability.NOT_APPLICABLE: NOT_APPLICABLE,
    }.get(measure.applicability, "")


def _row(measure: Measure, context: Applicable) -> dict[str, Any]:
    card = context.catalog.get(measure.id)
    assert card is not None
    selection = context.selections[measure.id]
    titles = {r.id: r.title for r in card.reinforcements}
    state = _state(measure, context)
    absent = absent_asset_types().get(measure.id, ()) if selection.required else ()
    compensation = measure.compensation
    return {
        "id": measure.id,
        "name": card.name,
        "state": state,
        "reason": STATE_REASONS.get(state),
        "required": selection.required,
        "required_by": selection.required_by,
        "required_reinforcements": [
            {"id": r, "title": titles.get(r, "")} for r in selection.reinforcements
        ],
        "groups": [
            {
                "options": [{"id": r, "title": titles.get(r, "")} for r in group],
                "chosen": next((r for r in measure.chosen_reinforcements if r in group), ""),
            }
            for group in selection.alternatives
        ],
        "choice": _choice(measure),
        "justification": measure.justification or "",
        "absent_types": list(absent),
        "absent_type": measure.absent_assets.asset_type if measure.absent_assets else "",
        "obligations": [
            {"id": o, "title": titles.get(o, "Parte general de la medida")}
            for o in (
                BASE,
                *selection.reinforcements,
                *(r for g in selection.alternatives for r in g),
            )
        ]
        if selection.required
        else [],
        "compensation": {
            "measures": "\n".join(compensation.measures),
            "replaces": list(compensation.replaces),
            "justification": compensation.justification,
        }
        if compensation
        else {"measures": "", "replaces": [], "justification": ""},
    }


def _rows(loaded: Loaded) -> list[dict[str, Any]]:
    if loaded.context is None or loaded.document is None:
        return []
    return [_row(m, loaded.context) for m in loaded.document.measures]


def _needs_preparing(loaded: Loaded) -> bool:
    if loaded.context is None:
        return False
    if loaded.document is None:
        return True
    return any(
        m.applicability is Applicability.UNDETERMINED and loaded.context.selections[m.id].required
        for m in loaded.document.measures
    )


# --- decidir las medidas -----------------------------------------------------------------------


def _medidas_context(step: StepInput) -> Context:
    loaded = _load(step.store, step.now)
    rows = _rows(loaded)
    return {
        "blocked": loaded.blocked,
        "needs_preparing": _needs_preparing(loaded),
        "category": loaded.context.category.value if loaded.context else None,
        "pending": [r for r in rows if r["state"] in PENDING_STATES],
        "required": [r for r in rows if r["state"] == "decided" and r["required"]],
        "own": [r for r in rows if r["state"] == "decided" and not r["required"]],
    }


def _prepare(step: StepInput) -> None:
    loaded = _load(step.store, step.now)
    if loaded.blocked:
        raise StepRejected(form_errors=[loaded.blocked])
    with plain_rejection(MeasuresFileError):
        init_measures(step.store)
    with plain_rejection(ApplicabilityRejected):
        propose_applicability(step.store, step.now)


def _plan_decision(form_row: dict[str, Any], step: StepInput) -> dict[str, Any]:
    """Los argumentos de `decide` para el formulario de una medida; nada se escribe si falla."""
    form = step.form
    choice = first(form, "decision")
    allowed = [APPLIES]
    if form_row["absent_types"]:
        allowed.append(ABSENT)
    if not form_row["required"]:
        allowed.append(NOT_APPLICABLE)
    if choice not in allowed:
        raise StepRejected(form_errors=["Elige qué decides para esta medida."])
    justification = first(form, "justification") or (
        form_row["justification"] if choice == APPLIES else ""
    )
    if not justification:
        raise StepRejected(form_errors=["Explica tu decisión: la justificación es obligatoria."])
    arguments: dict[str, Any] = {
        "applicability": Applicability.APPLICABLE
        if choice == APPLIES
        else Applicability.NOT_APPLICABLE,
        "justification": justification,
        "chosen_reinforcements": _chosen(form_row, step) if choice == APPLIES else [],
    }
    if choice == ABSENT:
        asset_type = first(form, "absent_asset_type")
        if asset_type not in form_row["absent_types"]:
            raise StepRejected(form_errors=["Elige el tipo de activo que no existe."])
        arguments["absent_assets"] = {"asset_type": asset_type, "justification": justification}
    if choice == APPLIES and (compensation := _compensation(step)):
        arguments["compensation"] = compensation
    return arguments


def _chosen(form_row: dict[str, Any], step: StepInput) -> list[str]:
    chosen = []
    for index, group in enumerate(form_row["groups"]):
        picked = first(step.form, f"reinforcement~{index}")
        if picked and picked not in [o["id"] for o in group["options"]]:
            raise StepRejected(form_errors=["Elige uno de los refuerzos de la lista."])
        if picked:
            chosen.append(picked)
    return chosen


def _compensation(step: StepInput) -> dict[str, Any] | None:
    form = step.form
    measures = [line.strip() for line in first(form, "comp_measures").splitlines() if line.strip()]
    replaces, justification = form.get("comp_replaces", []), first(form, "comp_justification")
    if not (measures or replaces or justification):
        return None
    problems = []
    if not measures:
        problems.append("Describe las medidas compensatorias, una por línea.")
    if not replaces:
        problems.append("Marca qué obligaciones de la medida sustituyen.")
    if not justification:
        problems.append("Explica por qué protegen igual o mejor.")
    if problems:
        raise StepRejected(form_errors=problems)
    return {"measures": measures, "replaces": replaces, "justification": justification}


def _decidir(step: StepInput) -> None:
    loaded = _load(step.store, step.now)
    if loaded.blocked:
        raise StepRejected(form_errors=[loaded.blocked])
    if loaded.document is None:
        raise StepRejected(form_errors=[NEEDS_PREPARING])
    row = next((r for r in _rows(loaded) if r["id"] == first(step.form, "measure_id")), None)
    if row is None:
        raise StepRejected(form_errors=["Esa medida no existe en el catálogo."])
    arguments = _plan_decision(row, step)
    with plain_rejection(ApplicabilityRejected):
        decide(step.store, step.now, row["id"], **arguments)


def _medidas_save(step: StepInput) -> None:
    action = first(step.form, "accion")
    if action == "preparar":
        _prepare(step)
    elif action == "decidir":
        _decidir(step)


def _medidas_done(store: UiStore, now: datetime) -> bool:
    loaded = _load(store, now)
    return (
        loaded.document is not None
        and not _needs_preparing(loaded)
        and not any(r["state"] in PENDING_STATES for r in _rows(loaded))
    )


# --- firmar la Declaración ---------------------------------------------------------------------


def _aprobacion_context(step: StepInput) -> Context:
    status = declaration_status(step.store, step.now)
    issues: list[str] = []
    exclusions = []
    try:
        validation = validate_applicability(step.store, step.now)
        issues = plain_errors(validation.errors)
        exclusions = [
            {
                "id": e.id,
                "name": e.name,
                "asset_type": e.asset_type,
                "justification": e.justification,
            }
            for e in validation.exclusions
        ]
    except ScopeNotApproved:
        issues = [SCOPE_NOT_APPROVED]
    except CategorizationNotCurrent:
        issues = [CATEGORIZATION_NOT_CURRENT]
    declaration = status.declaration
    return {
        "declaration_state": status.state.value,
        "stale_reasons": plain_errors(status.errors)
        if status.state in (DeclarationState.STALE, DeclarationState.INVALID)
        else [],
        "issues": issues[:MAX_LISTED_ERRORS],
        "issues_more": max(0, len(issues) - MAX_LISTED_ERRORS),
        "exclusions": exclusions,
        "declaration": {
            "approved_by": declaration.approved_by,
            "approved_at": format_instant(declaration.approved_at),
            "selected_count": len(declaration.selected),
        }
        if declaration and status.state is DeclarationState.CURRENT
        else None,
    }


def _aprobacion_save(step: StepInput) -> None:
    if first(step.form, "accion") != "aprobar":
        return
    by = declared_name(first(step.form, "approved_by"), "Quién la firma", "approved_by")
    try:
        approve_declaration(step.store, by, step.now)
    except ScopeNotApproved:
        raise StepRejected(form_errors=[SCOPE_NOT_APPROVED]) from None
    except CategorizationNotCurrent:
        raise StepRejected(form_errors=[CATEGORIZATION_NOT_CURRENT]) from None
    except DeclarationRejected as exc:
        raise StepRejected(form_errors=plain_errors(exc.errors)) from None


def _aprobacion_done(store: UiStore, now: datetime) -> bool:
    return declaration_status(store, now).state is DeclarationState.CURRENT


APPLICABILITY_STEPS = (
    Step("aplicabilidad", "Medidas", "Qué medidas te aplican", "aplicabilidad",
         "wizard/aplicabilidad_medidas.html", _medidas_done, _medidas_context, _medidas_save),
    Step("aprobacion-aplicabilidad", "Firmar la Declaración",
         "Firmar la Declaración de Aplicabilidad", "aplicabilidad",
         "wizard/aplicabilidad_aprobacion.html", _aprobacion_done, _aprobacion_context,
         _aprobacion_save),
)  # fmt: skip
