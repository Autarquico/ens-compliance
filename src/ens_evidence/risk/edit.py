"""Edición de `risk/` sin tocar el YAML a mano.

Cada edición valida contra los modelos, la metodología y el inventario, conserva la cabecera de
comentarios de la plantilla y no aprueba nada: la aprobación guarda el sha256 de los ficheros,
así que cualquier cambio la deja obsoleta (risk status). Nunca se rellena un valor por la
persona: ni escalas, ni niveles, ni responsables.
"""

from datetime import datetime
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError

from ens_evidence.categorization.edit import _split_header
from ens_evidence.risk.init import init_risk
from ens_evidence.risk.models import (
    METHODOLOGY_KEY,
    METHODOLOGY_MD_KEY,
    REGISTER_KEY,
    TREATMENT_PLAN_KEY,
    Methodology,
    RegisterFile,
    Risk,
    TreatmentAction,
    TreatmentPlanFile,
)
from ens_evidence.risk.templates import METHODOLOGY_MD
from ens_evidence.risk.validate import _check_register
from ens_evidence.scope.inventory import load_inventory
from ens_evidence.scope.messages import describe_validation
from ens_evidence.scope.secrets import find_secrets
from ens_evidence.workspace import NotFound, WorkspaceStore
from ens_evidence.yaml_unique import safe_load_unique


class RiskEditRejected(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


def _read(store: WorkspaceStore, key: str) -> tuple[str, dict[str, Any]]:
    try:
        text = store.read_text(key)
    except NotFound:
        raise RiskEditRejected([f"{key}: no existe; créalo con risk init"]) from None
    header, body = _split_header(text)
    try:
        raw = safe_load_unique(body)
    except yaml.YAMLError:
        raise RiskEditRejected([f"{key}: no es YAML válido; revisa la sintaxis"]) from None
    if not isinstance(raw, dict):
        raise RiskEditRejected([f"{key}: no tiene la forma esperada"])
    return header, raw


def _checked[M: BaseModel](key: str, model: type[M], document: dict[str, Any]) -> M:
    try:
        return model.model_validate(document)
    except ValidationError as exc:
        raise RiskEditRejected(describe_validation(key, exc, document)) from None


def _save(
    store: WorkspaceStore,
    key: str,
    header: str,
    model: type[BaseModel],
    document: dict[str, Any],
) -> None:
    _checked(key, model, document)
    body = yaml.safe_dump(document, sort_keys=False, allow_unicode=True, default_flow_style=False)
    store.write_text(key, header + body)


def _reject_secrets(key: str, fields: dict[str, Any]) -> None:
    """Rechaza antes de escribir; nombra el campo, nunca el valor."""
    if leaked := find_secrets(fields):
        raise RiskEditRejected(
            [f"{key}: {path} parece contener un secreto; quítalo" for path in leaked]
        )


def _normalized(model: type[BaseModel], key: str, fields: dict[str, Any]) -> dict[str, Any]:
    """El elemento validado, con sus claves en el orden del modelo y sin nulos."""
    try:
        return model.model_validate(fields).model_dump(mode="json", exclude_none=True)
    except ValidationError as exc:
        raise RiskEditRejected(describe_validation(key, exc, fields)) from None


def _ids(items: list[Any]) -> set[Any]:
    return {item.get("id") for item in items if isinstance(item, dict)}


def adopt_methodology(
    store: WorkspaceStore, name: str, reference: str, adaptation: str
) -> list[str]:
    """Declara la metodología de la organización y la marca adoptada. Devuelve lo creado.

    Crea las plantillas de risk/ si faltan; las escalas y la matriz se conservan como estén.
    """
    fields = {"name": name, "reference": reference, "adaptation": adaptation}
    _reject_secrets(METHODOLOGY_KEY, fields)
    created, _ = init_risk(store)
    header, document = _read(store, METHODOLOGY_KEY)
    document.update(adopted=True, **fields)
    _save(store, METHODOLOGY_KEY, header, Methodology, document)
    _document_methodology(store, fields)
    return created


def _document_methodology(store: WorkspaceStore, fields: dict[str, str]) -> None:
    """Rellena la cabecera de `risk/methodology.md` si sigue siendo la plantilla; nunca la pisa."""
    if store.read_text(METHODOLOGY_MD_KEY) != METHODOLOGY_MD:
        return
    filled = METHODOLOGY_MD
    for label, value in [
        ("Nombre y versión", fields["name"]),
        ("Referencia (norma o guía publicada)", fields["reference"]),
        ("Adaptaciones respecto a la referencia", fields["adaptation"]),
    ]:
        filled = filled.replace(f"- {label}:\n", f"- {label}: {value}\n", 1)
    store.write_text(METHODOLOGY_MD_KEY, filled)


def add_risk(store: WorkspaceStore, fields: dict[str, Any], now: datetime) -> None:
    """Añade un riesgo al registro, validado contra la metodología y el inventario."""
    _reject_secrets(REGISTER_KEY, fields)
    risk = _normalized(Risk, REGISTER_KEY, fields)
    header, register = _read(store, REGISTER_KEY)
    current = register.get("risks") or []
    if risk["id"] in _ids(current):
        raise RiskEditRejected([f"{REGISTER_KEY}: el id {risk['id']} ya existe"])
    _check_against_scope(store, risk, now)
    register["risks"] = [*current, risk]
    _save(store, REGISTER_KEY, header, RegisterFile, register)


def _check_against_scope(store: WorkspaceStore, risk: dict[str, Any], now: datetime) -> None:
    _, raw = _read(store, METHODOLOGY_KEY)
    methodology = _checked(METHODOLOGY_KEY, Methodology, raw)
    inventory = load_inventory(store)
    if inventory.inventory is None:
        raise RiskEditRejected(inventory.errors)
    errors = _check_register(
        RegisterFile(risks=[Risk.model_validate(risk)]),
        methodology,
        {a.id for a in inventory.inventory.assets.assets},
        {s.id for s in inventory.inventory.services.services},
        now,
    )
    if errors:
        raise RiskEditRejected(errors)


def remove_risk(store: WorkspaceStore, risk_id: str) -> None:
    """Quita un riesgo. Se niega si alguna acción del plan lo cita: primero se quitan ellas."""
    header, register = _read(store, REGISTER_KEY)
    current = register.get("risks") or []
    if risk_id not in _ids(current):
        raise RiskEditRejected([f"{REGISTER_KEY}: no hay ningún riesgo con id {risk_id}"])
    _, plan = _read(store, TREATMENT_PLAN_KEY)
    citing = [a.get("id") for a in plan.get("actions") or [] if a.get("risk") == risk_id]
    if citing:
        raise RiskEditRejected(
            [
                f"{TREATMENT_PLAN_KEY}: las acciones {', '.join(map(str, citing))} citan "
                f"{risk_id}; quítalas antes"
            ]
        )
    register["risks"] = [r for r in current if r.get("id") != risk_id]
    _save(store, REGISTER_KEY, header, RegisterFile, register)


def add_action(store: WorkspaceStore, fields: dict[str, Any]) -> None:
    """Añade una acción al plan de tratamiento; el riesgo que cita debe existir."""
    _reject_secrets(TREATMENT_PLAN_KEY, fields)
    action = _normalized(TreatmentAction, TREATMENT_PLAN_KEY, fields)
    _, register = _read(store, REGISTER_KEY)
    if action["risk"] not in _ids(register.get("risks") or []):
        raise RiskEditRejected(
            [f"{TREATMENT_PLAN_KEY}: el riesgo {action['risk']} no existe en {REGISTER_KEY}"]
        )
    header, plan = _read(store, TREATMENT_PLAN_KEY)
    current = plan.get("actions") or []
    if action["id"] in _ids(current):
        raise RiskEditRejected([f"{TREATMENT_PLAN_KEY}: el id {action['id']} ya existe"])
    plan["actions"] = [*current, action]
    _save(store, TREATMENT_PLAN_KEY, header, TreatmentPlanFile, plan)


def remove_action(store: WorkspaceStore, action_id: str) -> None:
    """Quita una acción del plan de tratamiento."""
    header, plan = _read(store, TREATMENT_PLAN_KEY)
    current = plan.get("actions") or []
    if action_id not in _ids(current):
        raise RiskEditRejected([f"{TREATMENT_PLAN_KEY}: no hay ninguna acción con id {action_id}"])
    plan["actions"] = [a for a in current if a.get("id") != action_id]
    _save(store, TREATMENT_PLAN_KEY, header, TreatmentPlanFile, plan)
