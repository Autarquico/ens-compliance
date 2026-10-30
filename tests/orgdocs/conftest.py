from pathlib import Path

import pytest
import yaml

from ens_evidence.models.enums import Role
from ens_evidence.orgdocs import build_documents
from ens_evidence.scope import approve_scope
from ens_evidence.workspace import LocalStore

from .orgdocs_helpers import APPROVED_AT, EXAMPLE_SCOPE, SCOPE_FILES, fixture_answers, write_answers


@pytest.fixture
def scoped(tmp_path) -> LocalStore:
    """Workspace con el alcance de ejemplo aprobado y sin respuestas."""
    workspace = LocalStore(tmp_path / "ws")
    for name in SCOPE_FILES:
        workspace.write_text(
            f"scope/{name}.yaml", (EXAMPLE_SCOPE / f"{name}.yaml").read_text(encoding="utf-8")
        )
    approve_scope(workspace, "Ana Ruiz", Role.SYSTEM_OWNER, APPROVED_AT)
    return workspace


@pytest.fixture
def answered(scoped) -> LocalStore:
    write_answers(scoped, fixture_answers())
    return scoped


@pytest.fixture
def built(answered) -> LocalStore:
    build_documents(answered)
    return answered


@pytest.fixture
def settings(tmp_path) -> Path:
    path = tmp_path / "settings.yaml"
    path.write_text(
        yaml.safe_dump({"workspace": {"backend": "local", "local": {"path": str(tmp_path / "ws")}}})
    )
    return path
