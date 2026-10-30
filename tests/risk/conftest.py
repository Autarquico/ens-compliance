import pytest

from ens_evidence.categorization import approve_categorization
from ens_evidence.models.enums import Role
from ens_evidence.risk import init_risk
from ens_evidence.scope import approve_scope
from ens_evidence.workspace import LocalStore

from .helpers import (
    EXAMPLE_SCOPE,
    METHODOLOGY,
    METHODOLOGY_MD,
    NOW,
    SCOPE_FILES,
    action,
    adopted_methodology,
    dump,
    risk,
    write_category,
    write_risks,
)


@pytest.fixture
def inventory_store(tmp_path) -> LocalStore:
    workspace = LocalStore(tmp_path / "ws")
    for name in SCOPE_FILES:
        workspace.write_text(
            f"scope/{name}.yaml", (EXAMPLE_SCOPE / f"{name}.yaml").read_text(encoding="utf-8")
        )
    approve_scope(workspace, "Ana Ruiz", Role.SYSTEM_OWNER, NOW)
    return workspace


@pytest.fixture
def store(inventory_store) -> LocalStore:
    """Alcance y categorización aprobados; análisis válido (metodología, un riesgo y una acción)."""
    write_category(inventory_store, "MEDIO")
    approve_categorization(inventory_store, "Marta Gil", NOW)
    init_risk(inventory_store)
    inventory_store.write_text(METHODOLOGY_MD, "# Metodología\n\nLa que usa la organización.\n")
    dump(inventory_store, METHODOLOGY, adopted_methodology())
    write_risks(inventory_store, [risk()], [action()])
    return inventory_store
