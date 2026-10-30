import pytest

from ens_evidence.models.enums import Role
from ens_evidence.scope import approve_scope
from ens_evidence.workspace import LocalStore

from .helpers import EXAMPLE_SCOPE, NOW, SCOPE_FILES


@pytest.fixture
def store(tmp_path) -> LocalStore:
    """Workspace con el inventario de ejemplo y el alcance aprobado."""
    workspace = LocalStore(tmp_path / "ws")
    for name in SCOPE_FILES:
        workspace.write_text(
            f"scope/{name}.yaml", (EXAMPLE_SCOPE / f"{name}.yaml").read_text(encoding="utf-8")
        )
    approve_scope(workspace, "Ana Ruiz", Role.SYSTEM_OWNER, NOW)
    return workspace
