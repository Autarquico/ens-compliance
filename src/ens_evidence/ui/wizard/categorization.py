"""Pasos de categorización: informaciones, valoraciones y su aprobación."""

from datetime import datetime

from ens_evidence.catalog.model import Dimension
from ens_evidence.categorization import (
    CATEGORIZATION_KEY,
    CategorizationApprovalRejected,
    CategorizationState,
    approve_categorization,
    categorization_status,
    init_categorization,
)
from ens_evidence.categorization.edit import (
    CategorizationEditRejected,
    add_information,
    set_rating,
    set_valuation,
)
from ens_evidence.scope import ScopeNotApproved
from ens_evidence.scope.inventory import SERVICES_KEY
from ens_evidence.ui.forms import first
from ens_evidence.ui.store import UiStore
from ens_evidence.ui.views import format_instant
from ens_evidence.ui.wizard.base import (
    Context,
    Step,
    StepInput,
    StepRejected,
    items,
    read_yaml,
    rejected_to_step,
    required,
)

LEVELS = ("BAJO", "MEDIO", "ALTO", "not_affected")
KINDS = (("information", "information"), ("service", "services"))
ROLE_LABELS = {
    "information": "del responsable de la información",
    "service": "del responsable del servicio",
}
SCOPE_NOT_APPROVED = "El alcance no está aprobado ahora: apruébalo en el paso «Aprobar el alcance»."


def _elements(store: UiStore) -> list[dict]:
    document = read_yaml(store, CATEGORIZATION_KEY)
    names = {
        s["id"]: str(s.get("name") or "")
        for s in items(read_yaml(store, SERVICES_KEY), "services")
        if "id" in s
    }
    elements = []
    for kind, list_name in KINDS:
        for item in items(document, list_name):
            if "id" not in item:
                continue
            ratings = {
                str(code): {
                    "level": str(r.get("level") or ""),
                    "justification": str(r.get("justification") or ""),
                }
                for code, r in (item.get("ratings") or {}).items()
                if isinstance(r, dict)
            }
            valuation = item.get("valuation")
            elements.append(
                {
                    "kind": kind,
                    "id": str(item["id"]),
                    "label": str(item.get("description") or names.get(item["id"]) or item["id"]),
                    "role_label": ROLE_LABELS[kind],
                    "ratings": ratings,
                    "valuation": {
                        "valued_by": str(valuation.get("valued_by") or ""),
                        "valued_at": format_instant(_parse_instant(valuation.get("valued_at"))),
                    }
                    if isinstance(valuation, dict)
                    else None,
                }
            )
    return elements


# --- información -------------------------------------------------------------------------------


def _informacion_context(step: StepInput) -> Context:
    document = read_yaml(step.store, CATEGORIZATION_KEY)
    return {
        "information": [
            {"id": str(i["id"]), "description": str(i.get("description") or "")}
            for i in items(document, "information")
            if "id" in i
        ]
    }


def _informacion_save(step: StepInput) -> None:
    form = step.form
    if not (first(form, "info_id") or first(form, "description")):
        return
    values = required(form, {"info_id": "El identificador", "description": "La descripción"})
    if not step.store.exists(CATEGORIZATION_KEY):
        try:
            init_categorization(step.store)
        except ScopeNotApproved:
            raise StepRejected(form_errors=[SCOPE_NOT_APPROVED]) from None
    with rejected_to_step(CategorizationEditRejected):
        add_information(step.store, values["info_id"], values["description"], step.now)


def _informacion_done(store: UiStore, now: datetime) -> bool:
    return bool(items(read_yaml(store, CATEGORIZATION_KEY), "information"))


# --- valoraciones ------------------------------------------------------------------------------


def _field(kind: str, element_id: str, code: str, part: str) -> str:
    return f"rating~{kind}~{element_id}~{code}~{part}"


def _planned_ratings(step: StepInput, elements: list[dict]) -> tuple[list, dict]:
    """Valoraciones que cambian y errores de los campos; nada se escribe si hay errores."""
    changes: list[tuple[str, str, Dimension, str, str]] = []
    errors: dict[str, list[str]] = {}
    for element in elements:
        for dimension in Dimension:
            level_name = _field(element["kind"], element["id"], dimension.value, "level")
            just_name = _field(element["kind"], element["id"], dimension.value, "justification")
            level, justification = first(step.form, level_name), first(step.form, just_name)
            current = element["ratings"].get(dimension.value)
            if not level:
                if justification and not current:
                    errors[level_name] = ["Elige un nivel o borra la justificación."]
                continue
            if level not in LEVELS:
                errors[level_name] = ["Elige uno de los niveles de la lista."]
            elif not justification:
                errors[just_name] = ["Cada nivel necesita su justificación."]
            elif not current or (current["level"], current["justification"]) != (
                level,
                justification,
            ):
                changes.append((element["kind"], element["id"], dimension, level, justification))
    return changes, errors


def _valuations_due(step: StepInput, elements: list[dict], changes: list) -> list:
    """Quién valora cada elemento completo; no firma los que tienen dimensiones pendientes."""
    rated = {(kind, element_id, dimension.value) for kind, element_id, dimension, _, _ in changes}
    due = []
    for element in elements:
        signer = first(step.form, f"valued_by~{element['kind']}~{element['id']}")
        complete = all(
            d.value in element["ratings"] or (element["kind"], element["id"], d.value) in rated
            for d in Dimension
        )
        current = element["valuation"]
        if signer and complete and (not current or current["valued_by"] != signer):
            due.append((element["kind"], element["id"], signer))
    return due


def _valoraciones_context(step: StepInput) -> Context:
    return {"elements": _elements(step.store)}


def _valoraciones_save(step: StepInput) -> None:
    elements = _elements(step.store)
    changes, errors = _planned_ratings(step, elements)
    if errors:
        raise StepRejected(errors, ["Revisa los campos marcados."])
    due = _valuations_due(step, elements, changes)
    for kind, element_id, dimension, level, justification in changes:
        with rejected_to_step(CategorizationEditRejected):
            set_rating(step.store, kind, element_id, dimension, level, justification, step.now)
    for kind, element_id, signer in due:
        with rejected_to_step(CategorizationEditRejected):
            set_valuation(step.store, kind, element_id, signer, step.now)


def _valoraciones_done(store: UiStore, now: datetime) -> bool:
    return categorization_status(store, now).state in (
        CategorizationState.NOT_APPROVED,
        CategorizationState.STALE,
        CategorizationState.EXPIRED,
        CategorizationState.CURRENT,
    )


# --- aprobación de la categorización -----------------------------------------------------------


PENDING_KINDS = (("information/", "Información "), ("service/", "Servicio "))


def _plain_pending(item: str) -> str:
    for prefix, label in PENDING_KINDS:
        if item.startswith(prefix):
            return label + item.removeprefix(prefix)
    return item


def _parse_instant(value: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _aprobacion_context(step: StepInput) -> Context:
    status = categorization_status(step.store, step.now)
    computation = status.computation
    approval = status.approval
    return {
        "categorization_state": status.state.value,
        "category": computation.category.value if computation and computation.category else None,
        "dimension_levels": [
            {
                "code": item.dimension.value,
                "level": item.level.value if item.level else "not_affected",
            }
            for item in (computation.levels if computation else ())
        ],
        "pending": [_plain_pending(p) for p in status.pending],
        "issues": status.errors if status.state is CategorizationState.INVALID else [],
        "approval": {
            "approved_by": approval.approved_by,
            "approved_at": format_instant(approval.approved_at),
        }
        if approval and status.state is CategorizationState.CURRENT
        else None,
    }


def _aprobacion_save(step: StepInput) -> None:
    if first(step.form, "accion") != "aprobar":
        return
    by = required(step.form, {"approved_by": "Quién lo aprueba"})["approved_by"]
    try:
        approve_categorization(step.store, by, step.now)
    except ScopeNotApproved:
        raise StepRejected(form_errors=[SCOPE_NOT_APPROVED]) from None
    except CategorizationApprovalRejected as exc:
        raise StepRejected(form_errors=exc.errors) from None


def _aprobacion_done(store: UiStore, now: datetime) -> bool:
    return categorization_status(store, now).state is CategorizationState.CURRENT


CATEGORIZATION_STEPS = (
    Step("informacion", "Información", "Qué información maneja el sistema", "categorizacion",
         "wizard/informacion.html", _informacion_done, _informacion_context, _informacion_save),
    Step("valoraciones", "Valoraciones", "Cuánto perjuicio causaría un fallo", "categorizacion",
         "wizard/valoraciones.html", _valoraciones_done, _valoraciones_context, _valoraciones_save),
    Step("aprobacion-categorizacion", "Aprobar la categorización", "Aprobar la categorización",
         "categorizacion", "wizard/aprobacion_categorizacion.html", _aprobacion_done,
         _aprobacion_context, _aprobacion_save),
)  # fmt: skip
