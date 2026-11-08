"""Pasos de riesgos: metodología, registro de riesgos y acciones, y su aprobación."""

import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from typing import Any

import yaml
from pydantic import ValidationError

from ens_evidence.catalog import load_catalog
from ens_evidence.catalog.model import Dimension, SecurityLevel
from ens_evidence.categorization import CategorizationState, categorization_status
from ens_evidence.models.enums import Role
from ens_evidence.risk import RiskApprovalRejected, RiskState, approve_risk, risk_status
from ens_evidence.risk.edit import (
    RiskEditRejected,
    add_action,
    add_risk,
    adopt_methodology,
    remove_action,
    remove_risk,
)
from ens_evidence.risk.models import (
    METHODOLOGY_KEY,
    METHODOLOGY_MD_KEY,
    REGISTER_KEY,
    TREATMENT_PLAN_KEY,
    ActionStatus,
    Methodology,
    Treatment,
)
from ens_evidence.risk.templates import METHODOLOGY_MD, METHODOLOGY_YAML
from ens_evidence.risk.validate import validate_risk
from ens_evidence.scope import ScopeNotApproved
from ens_evidence.scope.inventory import SERVICES_KEY, SYSTEM_KEY
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
    required,
)

SCOPE_NOT_APPROVED = "El alcance no está aprobado ahora: apruébalo en el paso «Aprobar el alcance»."
CATEGORIZATION_NOT_CURRENT = (
    "La categorización no está vigente: apruébala en el paso «Aprobar la categorización»."
)
NOT_ADOPTED = "Primero adopta la metodología en el paso «Metodología»."
MAX_LISTED_ERRORS = 15

TREATMENT_LABELS = {
    Treatment.MITIGATE: "Reducirlo con medidas",
    Treatment.ACCEPT: "Aceptarlo",
    Treatment.TRANSFER: "Transferirlo (un seguro o un proveedor)",
    Treatment.AVOID: "Evitarlo (dejar de hacer lo que lo causa)",
}
ROLE_LABELS = {
    Role.SYSTEM_OWNER: "Responsable del sistema",
    Role.MANAGEMENT: "Dirección",
    Role.SECURITY_OFFICER: "Responsable de la seguridad",
    Role.INFORMATION_OWNER: "Responsable de la información",
    Role.SERVICE_OWNER: "Responsable del servicio",
    Role.SYSTEM_ADMINISTRATOR: "Administrador del sistema",
    Role.SYSTEM_ADMINISTRATOR_DELEGATE: "Administrador delegado",
}
ACTION_STATUS_LABELS = {
    ActionStatus.PENDING: "Pendiente",
    ActionStatus.IN_PROGRESS: "En curso",
    ActionStatus.DONE: "Hecha",
    ActionStatus.CANCELLED: "Cancelada",
}
_PLAIN_REWRITES = (
    (re.compile(r"\s*\((?:ens-evidence )?(?:scope|categorization|risk|applicability|controls)"
                r"(?: [\w-]+)+\)"), ""),
    (re.compile(r"(?:revísala y )?confírmala con `?applicability propose --confirm [\w.]+`?"),
     "decídela otra vez con el botón de su ficha"),
    (re.compile(r"; (?:créalo|crea las plantillas) con (?:ens-evidence )?risk init"),
     "; adopta la metodología en el paso «Metodología»"),
    (re.compile(r"; adóptala con adopted: true"), "; adóptala en el paso «Metodología»"),
    (re.compile(r"; ejecuta controls init"),
     "; vuelve a preparar las medidas en el paso «Aplicabilidad»"),
    (re.compile(r"`chosen_reinforcements`"), "los refuerzos elegidos"),
    (re.compile(r"`absent_assets`"), "la ausencia de activo"),
    (re.compile(r"`compensation`"), "la compensación"),
    (re.compile(r"^[\w/.-]+\.yaml › "), ""),
)  # fmt: skip


def plain_errors(errors: list[str]) -> list[str]:
    """Los motivos de una API de producto, sin órdenes de CLI ni nombres de fichero de cabecera."""
    plain = []
    for error in errors:
        for pattern, replacement in _PLAIN_REWRITES:
            error = pattern.sub(replacement, error)
        plain.append(error[:1].upper() + error[1:])
    return plain


@contextmanager
def plain_rejection(rejection: type[Exception]) -> Iterator[None]:
    """Como `rejected_to_step`, pero con los motivos en lenguaje llano."""
    try:
        yield
    except rejection as exc:
        raise StepRejected(form_errors=plain_errors(list(getattr(exc, "errors", ())))) from None


def _methodology(store: UiStore) -> Methodology | None:
    try:
        return Methodology.model_validate(read_yaml(store, METHODOLOGY_KEY))
    except ValidationError:
        return None


def _adopted(store: UiStore) -> Methodology | None:
    methodology = _methodology(store)
    return methodology if methodology and methodology.adopted else None


def _scale(values: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [{"id": str(v.get("id")), "description": str(v.get("description"))} for v in values]


def _included(store: UiStore, name: str) -> list[str]:
    return [str(i) for i in read_yaml(store, SYSTEM_KEY).get(name) or []]


# --- metodología -------------------------------------------------------------------------------


def _metodologia_context(step: StepInput) -> Context:
    document = read_yaml(step.store, METHODOLOGY_KEY) or yaml.safe_load(METHODOLOGY_YAML)
    matrix = document.get("matrix") or {}
    impacts = [str(i.get("id")) for i in document.get("impact") or []]
    return {
        "values": {
            name: str(document.get(name) or "") for name in ("name", "reference", "adaptation")
        },
        "adopted": bool(document.get("adopted")),
        "likelihood": _scale(document.get("likelihood") or []),
        "impact": _scale(document.get("impact") or []),
        "risk_levels": _scale(document.get("risk_levels") or []),
        "impact_ids": impacts,
        "matrix_rows": [
            {"likelihood": str(likelihood), "levels": [row.get(i, "") for i in impacts]}
            for likelihood, row in matrix.items()
        ],
    }


def _document_methodology(step: StepInput, values: dict[str, str]) -> None:
    """Deja `risk/methodology.md` con lo declarado si aún es la plantilla (sin él no se aprueba)."""
    try:
        untouched = step.store.read_text(METHODOLOGY_MD_KEY) == METHODOLOGY_MD
    except Exception:
        return
    if untouched:
        step.store.write_text(
            METHODOLOGY_MD_KEY,
            "# Metodología de análisis de riesgos\n\n"
            "Declarada desde el asistente web. Las escalas y la matriz de riesgo están en "
            "`risk/methodology.yaml`.\n\n"
            f"- Nombre y versión: {values['name']}\n"
            f"- Referencia: {values['reference']}\n"
            f"- Adaptaciones respecto a la referencia: {values['adaptation']}\n",
        )


def _metodologia_save(step: StepInput) -> None:
    values = required(
        step.form,
        {
            "name": "El nombre de la metodología",
            "reference": "La referencia",
            "adaptation": "Lo que adaptas",
        },
    )
    with plain_rejection(RiskEditRejected):
        adopt_methodology(step.store, values["name"], values["reference"], values["adaptation"])
    _document_methodology(step, values)


def _metodologia_done(store: UiStore, now: datetime) -> bool:
    return _adopted(store) is not None


# --- riesgos y acciones ------------------------------------------------------------------------


def _catalog_choices() -> list[dict[str, Any]]:
    return [{"id": card.id, "label": card.name} for card in load_catalog().measures]


def _risk_rows(store: UiStore) -> list[dict[str, Any]]:
    actions = items(read_yaml(store, TREATMENT_PLAN_KEY), "actions")
    rows = []
    for risk in items(read_yaml(store, REGISTER_KEY), "risks"):
        acceptance = risk.get("acceptance")
        try:
            treatment = TREATMENT_LABELS[Treatment(risk.get("treatment"))]
        except ValueError:
            treatment = str(risk.get("treatment") or "")
        rows.append(
            {
                "id": str(risk.get("id")),
                "threat": str(risk.get("threat") or ""),
                "targets": [
                    *map(str, risk.get("assets") or []),
                    *map(str, risk.get("services") or []),
                ],
                "dimensions": [str(d) for d in risk.get("dimensions") or []],
                "likelihood": str(risk.get("likelihood") or ""),
                "impact": str(risk.get("impact") or ""),
                "inherent": str(risk.get("inherent") or ""),
                "residual": str(risk.get("residual") or ""),
                "treatment": treatment,
                "owner": str(risk.get("owner") or ""),
                "safeguards": [str(s) for s in risk.get("safeguards") or []],
                "accepted_by": str(acceptance.get("accepted_by"))
                if isinstance(acceptance, dict)
                else None,
                "actions": [_action_row(a) for a in actions if a.get("risk") == risk.get("id")],
            }
        )
    return rows


def _action_row(action: dict[str, Any]) -> dict[str, str]:
    try:
        status = ACTION_STATUS_LABELS[ActionStatus(action.get("status"))]
    except ValueError:
        status = str(action.get("status") or "")
    return {
        "id": str(action.get("id")),
        "description": str(action.get("description") or ""),
        "owner": str(action.get("owner") or ""),
        "due": str(action.get("due") or ""),
        "status": status,
    }


def _registro_context(step: StepInput) -> Context:
    methodology = _adopted(step.store)
    services = {
        str(s["id"]): str(s.get("name") or "")
        for s in items(read_yaml(step.store, SERVICES_KEY), "services")
        if "id" in s
    }
    return {
        "adopted": methodology is not None,
        "likelihood": _scale([v.model_dump() for v in methodology.likelihood])
        if methodology
        else [],
        "impact": _scale([v.model_dump() for v in methodology.impact]) if methodology else [],
        "risk_levels": _scale([v.model_dump() for v in methodology.risk_levels])
        if methodology
        else [],
        "minimum_level": methodology.minimum_level if methodology else "",
        "matrix_rows": [
            {"likelihood": likelihood, "levels": [row[i] for i in methodology.impact_ids]}
            for likelihood, row in methodology.matrix.items()
        ]
        if methodology
        else [],
        "impact_ids": methodology.impact_ids if methodology else [],
        "risks": _risk_rows(step.store),
        "actions": [
            _action_row(a) for a in items(read_yaml(step.store, TREATMENT_PLAN_KEY), "actions")
        ],
        "asset_choices": [
            {"id": a, "label": None} for a in _included(step.store, "included_assets")
        ],
        "service_choices": [
            {"id": s, "label": services.get(s) or None}
            for s in _included(step.store, "included_services")
        ],
        "safeguard_choices": _catalog_choices(),
        "levels": [level.value for level in SecurityLevel],
        "treatments": [{"value": t.value, "label": TREATMENT_LABELS[t]} for t in Treatment],
        "roles": [{"value": r.value, "label": label} for r, label in ROLE_LABELS.items()],
        "action_statuses": [
            {"value": s.value, "label": ACTION_STATUS_LABELS[s]} for s in ActionStatus
        ],
    }


def _planned_risk(step: StepInput, methodology: Methodology) -> dict[str, Any]:
    """Los campos del riesgo a partir del formulario; `StepRejected` con todos los fallos."""
    form, errors = step.form, {}
    fields: dict[str, Any] = {
        "id": first(form, "risk_id"),
        "threat": first(form, "threat"),
        "owner": first(form, "owner"),
        "assets": form.get("risk_assets", []),
        "services": form.get("risk_services", []),
        "safeguards": form.get("safeguards", []),
        "likelihood": first(form, "likelihood"),
        "impact": first(form, "impact"),
        "residual": first(form, "residual"),
        "treatment": first(form, "treatment"),
    }
    for name, label in (
        ("risk_id", "El identificador"),
        ("threat", "La amenaza"),
        ("owner", "El responsable"),
    ):
        key = {"risk_id": "id"}.get(name, name)
        if not fields[key]:
            errors[name] = [f"{label}: es obligatorio."]
    if not fields["assets"] and not fields["services"]:
        errors["risk_assets"] = ["Elige al menos un activo o un servicio afectado."]
    for name, allowed, label in (
        ("likelihood", methodology.likelihood_ids, "la probabilidad"),
        ("impact", methodology.impact_ids, "el impacto"),
        ("residual", methodology.risk_level_ids, "el riesgo que queda"),
        ("treatment", [t.value for t in Treatment], "el tratamiento"),
    ):
        if fields[name] not in allowed:
            errors[name] = [f"Elige {label} de la lista."]
    dimensions = [d for d in form.get("risk_dimensions", []) if d in Dimension]
    fields["dimensions"] = dimensions
    if not dimensions:
        errors["risk_dimensions"] = ["Elige al menos una dimensión afectada."]
    fields["valuation"] = {}
    for dimension in dimensions:
        level = first(form, f"valuation~{dimension}")
        if level in SecurityLevel:
            fields["valuation"][dimension] = level
        else:
            errors[f"valuation~{dimension}"] = ["Elige el nivel de esta dimensión."]
    if errors:
        raise StepRejected(errors)
    fields["inherent"] = methodology.inherent(fields["likelihood"], fields["impact"])
    if _needs_acceptance(fields, methodology):
        fields["acceptance"] = _acceptance(step)
    return fields


def _needs_acceptance(fields: dict[str, Any], methodology: Methodology) -> bool:
    return (
        fields["residual"] != methodology.minimum_level
        or fields["treatment"] == Treatment.ACCEPT.value
    )


def _acceptance(step: StepInput) -> dict[str, str]:
    by, role = first(step.form, "accepted_by"), first(step.form, "accepted_role")
    errors = {}
    if not by:
        errors["accepted_by"] = ["Quién acepta el riesgo que queda: es obligatorio."]
    if role not in Role:
        errors["accepted_role"] = ["Elige con qué rol lo acepta."]
    if errors:
        raise StepRejected(errors, ["Si queda riesgo, alguien tiene que aceptarlo."])
    return {"accepted_by": by, "role": role, "accepted_at": step.now.isoformat()}


def _add_risk(step: StepInput) -> None:
    methodology = _adopted(step.store)
    if methodology is None:
        raise StepRejected(form_errors=[NOT_ADOPTED])
    fields = _planned_risk(step, methodology)
    with plain_rejection(RiskEditRejected):
        add_risk(step.store, fields, step.now)


def _add_action(step: StepInput) -> None:
    if _adopted(step.store) is None:
        raise StepRejected(form_errors=[NOT_ADOPTED])
    form = step.form
    values = required(
        form,
        {
            "action_id": "El identificador",
            "action_risk": "El riesgo que trata",
            "action_description": "Qué se hará",
            "action_owner": "El responsable",
            "action_due": "La fecha límite",
        },
    )
    errors = {}
    try:
        date.fromisoformat(values["action_due"])
    except ValueError:
        errors["action_due"] = ["Escribe la fecha como AAAA-MM-DD."]
    status = first(form, "action_status") or ActionStatus.PENDING.value
    if status not in ActionStatus:
        errors["action_status"] = ["Elige uno de los estados de la lista."]
    if errors:
        raise StepRejected(errors)
    fields = {
        "id": values["action_id"],
        "risk": values["action_risk"],
        "description": values["action_description"],
        "owner": values["action_owner"],
        "due": values["action_due"],
        "status": status,
    }
    with plain_rejection(RiskEditRejected):
        add_action(step.store, fields)


def _registro_save(step: StepInput) -> None:
    action = first(step.form, "accion")
    if action == "anadir-riesgo":
        _add_risk(step)
    elif action == "anadir-accion":
        _add_action(step)
    elif action in ("quitar-riesgo", "quitar-accion"):
        item = required(step.form, {"item_id": "Qué quitar"})["item_id"]
        remove = remove_risk if action == "quitar-riesgo" else remove_action
        with plain_rejection(RiskEditRejected):
            remove(step.store, item)


def _registro_done(store: UiStore, now: datetime) -> bool:
    return bool(items(read_yaml(store, REGISTER_KEY), "risks"))


# --- aprobación --------------------------------------------------------------------------------


def _listed(errors: list[str]) -> tuple[list[str], int]:
    plain = plain_errors(errors)
    return plain[:MAX_LISTED_ERRORS], max(0, len(plain) - MAX_LISTED_ERRORS)


def _aprobacion_context(step: StepInput) -> Context:
    store, now = step.store, step.now
    status = risk_status(store, now)
    issues: list[str] = []
    if categorization_status(store, now).state is not CategorizationState.CURRENT:
        issues.append(CATEGORIZATION_NOT_CURRENT)
    try:
        _, errors = validate_risk(store, now)
        issues.extend(plain_errors(errors))
    except ScopeNotApproved:
        issues.append(SCOPE_NOT_APPROVED)
    approval = status.approval
    risks = items(read_yaml(store, REGISTER_KEY), "risks")
    return {
        "risk_state": status.state.value,
        "stale_reasons": plain_errors(status.errors)
        if status.state in (RiskState.STALE, RiskState.INVALID)
        else [],
        "issues": issues[:MAX_LISTED_ERRORS],
        "issues_more": max(0, len(issues) - MAX_LISTED_ERRORS),
        "risks_count": len(risks),
        "actions_count": len(items(read_yaml(store, TREATMENT_PLAN_KEY), "actions")),
        "approval": {
            "approved_by": approval.approved_by,
            "approved_at": format_instant(approval.approved_at),
            "valid_until": format_instant(approval.valid_until),
        }
        if approval and status.state is RiskState.APPROVED
        else None,
    }


def _aprobacion_save(step: StepInput) -> None:
    if first(step.form, "accion") != "aprobar":
        return
    by = required(step.form, {"approved_by": "Quién lo aprueba"})["approved_by"]
    try:
        approve_risk(step.store, by, step.now)
    except ScopeNotApproved:
        raise StepRejected(form_errors=[SCOPE_NOT_APPROVED]) from None
    except RiskApprovalRejected as exc:
        raise StepRejected(form_errors=plain_errors(exc.errors)) from None


def _aprobacion_done(store: UiStore, now: datetime) -> bool:
    return risk_status(store, now).state is RiskState.APPROVED


RISK_STEPS = (
    Step("riesgos-metodologia", "Metodología", "Cómo analizas los riesgos", "riesgos",
         "wizard/riesgos_metodologia.html", _metodologia_done, _metodologia_context,
         _metodologia_save),
    Step("riesgos", "Riesgos", "Qué puede salir mal y qué haces al respecto", "riesgos",
         "wizard/riesgos_registro.html", _registro_done, _registro_context, _registro_save),
    Step("aprobacion-riesgos", "Aprobar los riesgos", "Aprobar el análisis de riesgos", "riesgos",
         "wizard/riesgos_aprobacion.html", _aprobacion_done, _aprobacion_context,
         _aprobacion_save),
)  # fmt: skip
