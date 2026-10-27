import pytest

from ens_evidence.workspace import LocalStore

from .scope_helpers import EXAMPLE_SCOPE, SCOPE_FILES


@pytest.fixture
def store(tmp_path) -> LocalStore:
    workspace = LocalStore(tmp_path / "ws")
    for name in SCOPE_FILES:
        workspace.write_text(
            f"scope/{name}.yaml", (EXAMPLE_SCOPE / f"{name}.yaml").read_text(encoding="utf-8")
        )
    return workspace
