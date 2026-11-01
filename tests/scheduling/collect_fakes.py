"""Conector falso para `test_collect`: importable por nombre desde un subproceso `spawn`.

Se controla por entorno (el hijo lo hereda): `ENS_FAKE_MODE`, `ENS_FAKE_STATE` (directorio para
contadores y pid) y `ENS_FAKE_FAIL_FIRST` (cuántas llamadas fallan antes de dar un resultado sano).
"""

import os
import signal
import time
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from ens_evidence.connectors.base import Connector
from ens_evidence.connectors.github.config import GitHubConfig
from ens_evidence.models.contracts import CollectionResult
from ens_evidence.models.enums import CollectionStatus
from ens_evidence.workspace import WorkspaceError

CANARY = "CANARY-mensaje-con-token-ghp_" + "a" * 30
LEAK = "ghp_" + "b" * 30
LOCK_INODE_ENV = "ENS_FAKE_LOCK_INODE"


def _state() -> Path:
    return Path(os.environ["ENS_FAKE_STATE"])


def _calls() -> int:
    counter = _state() / "calls"
    count = int(counter.read_text()) + 1 if counter.exists() else 1
    counter.write_text(str(count))
    return count


def _result(status: CollectionStatus, data: dict) -> CollectionResult:
    return CollectionResult(
        source="github",
        collector="github",
        collected_at=datetime(2026, 10, 29, 9, tzinfo=UTC),
        asset_id="gh-taller",
        data=data,
        collection_status=status,
    )


def _inherits_lock() -> bool:
    inode = int(os.environ[LOCK_INODE_ENV])
    for fd in range(3, 256):
        try:
            if os.fstat(fd).st_ino == inode:
                return True
        except OSError:
            continue
    return False


class FakeConnector(Connector):
    name = "github"
    config_model = GitHubConfig

    @classmethod
    def from_settings(cls, settings):
        if os.environ.get("ENS_FAKE_MODE") == "plugin_load":
            raise RuntimeError(CANARY)
        return cls()

    def prepare(self, config: BaseModel, store) -> BaseModel:
        if os.environ.get("ENS_FAKE_MODE") == "prepare":
            raise RuntimeError(CANARY)
        if os.environ.get("ENS_FAKE_MODE") == "prepare_invalid":
            raise WorkspaceError("asset_id: debe ser un activo del inventario")
        return config

    def collect(self, config: BaseModel) -> CollectionResult:
        mode = os.environ.get("ENS_FAKE_MODE", "ok")
        failing = _calls() <= int(os.environ.get("ENS_FAKE_FAIL_FIRST", "1000"))
        if not failing or mode == "ok":
            return _result(CollectionStatus.SUCCESS, {"organization": "taller-norte"})
        match mode:
            case "collect":
                raise RuntimeError(CANARY)
            case "error":
                return _result(CollectionStatus.ERROR, {"error": "api_unavailable"})
            case "permission_denied":
                return _result(CollectionStatus.PERMISSION_DENIED, {"error": "forbidden"})
            case "not_supported":
                return _result(CollectionStatus.NOT_SUPPORTED, {"error": "not_supported"})
            case "secret":
                return _result(CollectionStatus.SUCCESS, {"leak": {"detail": LEAK}})
            case "secret_error":
                return _result(CollectionStatus.ERROR, {"error": f"fallo con {LEAK}"})
            case "hang":
                (_state() / "pid").write_text(str(os.getpid()))
                time.sleep(600)
            case "hang_ignoring_term":
                signal.signal(signal.SIGTERM, signal.SIG_IGN)
                (_state() / "pid").write_text(str(os.getpid()))
                time.sleep(600)
            case "die":
                os._exit(7)
            case "lock_fd":
                return _result(CollectionStatus.SUCCESS, {"inherited_lock": _inherits_lock()})
        raise AssertionError(mode)
