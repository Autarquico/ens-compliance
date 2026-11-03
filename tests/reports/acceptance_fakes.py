"""Conectores falsos del ciclo de aceptación: importables por nombre desde un subproceso `spawn`.

El hijo hereda el entorno: `ENS_Q1_MODES` es un JSON `{conector: modo}` y `ENS_Q1_COLLECTED_AT` el
instante de la recogida. Modos: `ok`, `raise` (excepción con el token del entorno en el mensaje),
`error` (ERROR devuelto), `hostile` (corpus en la fuente, el activo y los datos), `leak`
(secreto dentro de `data`), `reject` (`prepare` rechaza la configuración).
"""

import json
import os
from datetime import datetime, timedelta

from checks.test_expected import gh_evidence
from connectors.google_workspace.check_data import result as workspace_result
from connectors.google_workspace.check_data import user
from pydantic import BaseModel

from ens_evidence.connectors.base import Connector
from ens_evidence.connectors.cloudflare.config import CloudflareConfig
from ens_evidence.connectors.github.config import GitHubConfig
from ens_evidence.connectors.google_workspace.config import GoogleWorkspaceConfig
from ens_evidence.models.contracts import CollectionResult
from ens_evidence.models.enums import CollectionStatus
from ens_evidence.workspace import WorkspaceError

MODES_ENV = "ENS_Q1_MODES"
COLLECTED_AT_ENV = "ENS_Q1_COLLECTED_AT"
TOKEN_ENV = "GITHUB_TOKEN"
LEAK_ENV = "ENS_Q1_LEAK"
HOSTILE_ENV = "ENS_Q1_HOSTILE"
REJECTION = "asset_id: motivo-de-rechazo-legible"
ASSETS = {
    "github": "gh-taller",
    "cloudflare": "cf-taller",
    "google-workspace": "ws-identidad",
}
LIVE = {"facturas": False, "infra": False}
STALE = timedelta(days=25)


def _result(name: str, status: CollectionStatus, data: dict, collected_at: datetime):
    return CollectionResult(
        source=name,
        collector=name,
        collected_at=collected_at,
        asset_id=ASSETS[name],
        data=data,
        collection_status=status,
    )


def _hostile(name: str, at: datetime) -> CollectionResult:
    """Todo texto libre que un collector puede devolver lleva el corpus hostil (identidad incluida:
    el planificador la rechaza)."""
    corpus = json.loads(os.environ[HOSTILE_ENV])
    data = {
        "error": corpus[2],
        "sections": {corpus[3]: {"status": corpus[4], "reason": corpus[5], "items": [corpus[6]]}},
        corpus[1]: corpus[0],
    }
    return CollectionResult(
        source=corpus[0],
        collector=name,
        collected_at=at - STALE,
        asset_id=corpus[1],
        data=data,
        collection_status=CollectionStatus.SUCCESS,
    )


class _Fake(Connector):
    name: str
    config_model: type[BaseModel]

    @classmethod
    def from_settings(cls, settings):
        return cls()

    def prepare(self, config: BaseModel, store) -> BaseModel:
        if json.loads(os.environ[MODES_ENV])[self.name] == "reject":
            raise WorkspaceError(REJECTION)
        return config

    def collect(self, config: BaseModel) -> CollectionResult:
        mode = json.loads(os.environ[MODES_ENV])[self.name]
        at = datetime.fromisoformat(os.environ[COLLECTED_AT_ENV])
        match mode:
            case "raise":
                raise RuntimeError(f"token={os.environ[TOKEN_ENV]}")
            case "error":
                return _result(self.name, CollectionStatus.ERROR, {"error": "api_unavailable"}, at)
            case "leak":
                data = {"organization": "taller-norte", "detail": os.environ[LEAK_ENV]}
                return _result(self.name, CollectionStatus.SUCCESS, data, at)
            case "hostile":
                return _hostile(self.name, at)
            case "ok" if self.name == "github":
                return gh_evidence(LIVE, collected_at=at)
            case "ok" if self.name == "google-workspace":
                healthy = workspace_result([user("ana@taller.example")])
                return healthy.model_copy(
                    update={"asset_id": ASSETS[self.name], "collected_at": at}
                )
            case "ok":
                return _result(self.name, CollectionStatus.SUCCESS, {"organization": "x"}, at)
        raise AssertionError((self.name, mode))


class GitHubFake(_Fake):
    name = "github"
    config_model = GitHubConfig


class CloudflareFake(_Fake):
    name = "cloudflare"
    config_model = CloudflareConfig


class WorkspaceFake(_Fake):
    name = "google-workspace"
    config_model = GoogleWorkspaceConfig


FAKES = (GitHubFake, CloudflareFake, WorkspaceFake)
