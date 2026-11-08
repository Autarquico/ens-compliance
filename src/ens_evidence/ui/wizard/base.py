"""Piezas comunes de los pasos del asistente."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import yaml

from ens_evidence.ui.forms import Form, first
from ens_evidence.ui.store import UiStore
from ens_evidence.workspace import NotFound, NotText
from ens_evidence.yaml_unique import safe_load_unique

Context = dict[str, Any]


class StepRejected(Exception):
    """El paso no se guarda: errores por nombre de campo y errores generales."""

    def __init__(
        self, errors: dict[str, list[str]] | None = None, form_errors: list[str] | None = None
    ) -> None:
        super().__init__("paso rechazado")
        self.errors = errors or {}
        self.form_errors = form_errors or []


@dataclass(frozen=True)
class StepInput:
    store: UiStore
    now: datetime
    email: str
    query: dict[str, str] = field(default_factory=dict)
    form: Form = field(default_factory=dict)


@dataclass(frozen=True)
class Step:
    slug: str
    label: str
    title: str
    group: str
    template: str
    done: Callable[[UiStore, datetime], bool]
    context: Callable[[StepInput], Context]
    save: Callable[[StepInput], None]


def read_yaml(store: UiStore, key: str) -> dict[str, Any]:
    """El YAML como diccionario; vacío si falta o no se lee (la validación es de la API)."""
    try:
        raw = safe_load_unique(store.read_text(key))
    except (NotFound, NotText, yaml.YAMLError):
        return {}
    return raw if isinstance(raw, dict) else {}


def items(document: dict[str, Any], name: str) -> list[dict[str, Any]]:
    return [i for i in (document.get(name) or []) if isinstance(i, dict)]


def reject_removals(checked: set[str], included: list[str], field_name: str, what: str) -> None:
    if removed := sorted(set(included) - checked):
        raise StepRejected(
            {
                field_name: [
                    f"Quitar {what} del alcance todavía no se puede hacer aquí: "
                    f"{', '.join(removed)}."
                ]
            }
        )


def required(form: Form, names: dict[str, str]) -> dict[str, str]:
    """Valores de los campos obligatorios; `StepRejected` con todos los que falten."""
    values = {name: first(form, name) for name in names}
    missing = {
        name: [f"{label}: es obligatorio."] for name, label in names.items() if not values[name]
    }
    if missing:
        raise StepRejected(missing)
    return values
