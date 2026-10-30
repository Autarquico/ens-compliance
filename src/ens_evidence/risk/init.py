"""`risk init`: crea las plantillas de risk/ sin sobrescribir lo existente."""

from ens_evidence.risk.models import (
    METHODOLOGY_KEY,
    METHODOLOGY_MD_KEY,
    REGISTER_KEY,
    TREATMENT_PLAN_KEY,
)
from ens_evidence.risk.templates import (
    METHODOLOGY_MD,
    METHODOLOGY_YAML,
    REGISTER_YAML,
    TREATMENT_PLAN_YAML,
)
from ens_evidence.workspace import WorkspaceStore

_TEMPLATES = {
    METHODOLOGY_MD_KEY: METHODOLOGY_MD,
    METHODOLOGY_KEY: METHODOLOGY_YAML,
    REGISTER_KEY: REGISTER_YAML,
    TREATMENT_PLAN_KEY: TREATMENT_PLAN_YAML,
}


def init_risk(store: WorkspaceStore) -> tuple[list[str], list[str]]:
    """Devuelve (creados, conservados)."""
    created: list[str] = []
    kept: list[str] = []
    for key, text in _TEMPLATES.items():
        if store.exists(key):
            kept.append(key)
        else:
            store.write_text(key, text)
            created.append(key)
    return created, kept
