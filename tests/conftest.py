import os
import shutil
from datetime import UTC, datetime, timedelta

import pytest
import yaml
from scope.scope_helpers import APPROVED_AT, EXAMPLE_SCOPE, SCOPE_FILES

from ens_evidence.connectors.config import connector_config_key
from ens_evidence.models.enums import Role
from ens_evidence.scope import approve_scope
from ens_evidence.workspace import LocalStore

NOW = datetime(2026, 6, 1, tzinfo=UTC)
SHA = "a" * 64


@pytest.fixture(autouse=True, scope="session")
def _isolated_state_dir(tmp_path_factory):
    """El lock del ciclo vive en `ENS_STATE_DIR`: en los tests, nunca en el `~` real. De sesión,
    para cubrir también los fixtures de módulo y de sesión que ya ejecutan la CLI."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("ENS_STATE_DIR", str(tmp_path_factory.mktemp("state")))
        yield


@pytest.fixture
def now() -> datetime:
    return NOW


@pytest.fixture
def future() -> datetime:
    return NOW + timedelta(days=90)


@pytest.fixture
def past() -> datetime:
    return NOW - timedelta(days=1)


@pytest.fixture(scope="session")
def real_restic():
    """restic real; ausente = skip en local y fallo en CI."""
    if shutil.which("restic") is not None:
        return
    message = "restic no está instalado (brew install restic / apt-get install restic)"
    if os.environ.get("CI") == "true":
        pytest.fail(message)
    pytest.skip(message)


GITHUB_CONFIG = {"asset_id": "gh-taller", "organization": "taller-norte"}


@pytest.fixture
def store(tmp_path):
    """Un workspace con el alcance de ejemplo aprobado y GitHub configurado."""
    workspace = LocalStore(tmp_path / "ws")
    for name in SCOPE_FILES:
        workspace.write_text(
            f"scope/{name}.yaml", (EXAMPLE_SCOPE / f"{name}.yaml").read_text(encoding="utf-8")
        )
    approve_scope(workspace, "Ana Ruiz", Role.SYSTEM_OWNER, APPROVED_AT)
    workspace.write_text(connector_config_key("github"), yaml.safe_dump(GITHUB_CONFIG))
    return workspace
