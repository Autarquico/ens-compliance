from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import pytest
import yaml
from starlette.testclient import TestClient

from ens_evidence.agent.node_key import issue_enroll_token, issue_node_key
from ens_evidence.agent.protocol import AgentService
from ens_evidence.agent.registry import record_admission, record_revocation
from ens_evidence.agent.routes import routes
from ens_evidence.agent.secrets import AgentSecrets
from ens_evidence.agent.state import AgentState
from ens_evidence.http.app import create_app
from ens_evidence.workspace import LocalStore
from ens_evidence.workspace.cycle_lock import CycleLock

from .protocol_support import (
    ENDPOINT_CONFIG,
    LAPTOP,
    LAPTOP_UUID,
    NOW,
    Credentials,
    request_for,
    tree,
)

SECRETS = AgentSecrets(enroll_key=b"e" * 32, node_key_secret=b"n" * 32)


class Clock:
    def __init__(self) -> None:
        self.current = NOW

    def __call__(self):
        return self.current

    def advance(self, **delta: float) -> None:
        self.current += timedelta(**delta)


@dataclass
class World:
    workspace: LocalStore
    workspace_root: Path
    state_dir: Path
    state: AgentState
    clock: Clock
    client: TestClient

    def lock(self) -> CycleLock:
        return CycleLock("test-workspace", self.state_dir)

    def admit(self, asset: str = LAPTOP, hours: int = 24) -> None:
        with self.lock() as held:
            record_admission(
                self.workspace,
                held,
                asset,
                by="alex",
                now=self.clock(),
                expires_at=self.clock() + timedelta(hours=hours),
            )

    def revoke(self, asset: str = LAPTOP) -> None:
        with self.lock() as held:
            record_revocation(
                self.workspace, held, asset, by="alex", reason="robado", now=self.clock()
            )

    def credentials(self, generation: int = 1, uuid: str = LAPTOP_UUID, **fields) -> Credentials:
        expires = self.clock() + timedelta(hours=24)
        issued = {
            "node_key": issue_node_key(SECRETS, uuid, generation),
            "enroll_token": issue_enroll_token(SECRETS, uuid, generation, expires),
        }
        return Credentials(uuid=uuid, **{**issued, **fields})

    def enroll(self, asset: str = LAPTOP, **fields) -> str:
        """Admite el activo, da de alta el equipo y devuelve su node_key."""
        self.admit(asset)
        body = request_for("enroll", self.credentials(**fields))
        return self.post("enroll", body).json()["node_key"]

    def post(self, endpoint: str, body: dict):
        return self.client.post(f"/agent/{endpoint}", json=body)

    def files(self) -> dict[str, str]:
        return tree(self.workspace_root, self.state_dir)


@pytest.fixture
def world(tmp_path) -> World:
    workspace_root = tmp_path / "ws"
    workspace = LocalStore(workspace_root)
    workspace.write_text("connectors/endpoint.yaml", yaml.safe_dump(ENDPOINT_CONFIG))
    state_dir = tmp_path / "state"
    state = AgentState(state_dir)
    clock = Clock()
    holder: dict[str, World] = {}
    service = AgentService(workspace, SECRETS, state, lambda: holder["w"].lock(), now=clock)
    client = TestClient(create_app(routes(service)), raise_server_exceptions=True)
    holder["w"] = World(workspace, workspace_root, state_dir, state, clock, client)
    return holder["w"]
