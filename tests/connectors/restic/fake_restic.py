"""Ejecutor de restic falso: la única frontera simulada es el proceso."""

import json
from datetime import UTC, datetime

from ens_evidence.connectors.restic.process import CommandResult

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
REPOSITORY = "s3:s3.example.com/copias"
REPOSITORY_ID = "ab" * 32
OTHER_REPOSITORY_ID = "cd" * 32
SNAPSHOT_ID = "c1" * 32
PASSWORD = "contrasena-restic-canario-41x"
CANARY = "CANARIO-salida-restic-77"
HOSTNAME = "equipo-canario-hostname"
PATH_CANARY = "/home/canario/documentos-privados"


def config_json(repository_id: str = REPOSITORY_ID) -> str:
    return json.dumps({"version": 2, "id": repository_id, "chunker_polynomial": "3da3720705b3e5"})


def snapshot_json(snapshot_id: str = SNAPSHOT_ID, time: str = "2026-10-28T11:00:00.123456+01:00"):
    """Un elemento de `restic snapshots --json`, con campos que no deben copiarse."""
    return {
        "time": time,
        "tree": "f" * 64,
        "paths": [PATH_CANARY],
        "hostname": HOSTNAME,
        "username": "usuario-canario",
        "tags": ["ens-evidence"],
        "id": snapshot_id,
        "short_id": snapshot_id[:8],
    }


class FakeRestic:
    """Registra las llamadas y responde `cat config` y `snapshots` con lo configurado."""

    def __init__(
        self,
        *,
        cat: tuple[int, str] | None = None,
        snapshots: tuple[int, str] | None = None,
    ) -> None:
        self.cat = cat if cat is not None else (0, config_json())
        self.snapshots = snapshots if snapshots is not None else (0, "[]")
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, args: tuple[str, ...]) -> CommandResult:
        self.calls.append(args)
        code, stdout = self.cat if "cat" in args else self.snapshots
        return CommandResult(code, stdout)

    def with_snapshots(self, *items: dict) -> "FakeRestic":
        self.snapshots = (0, json.dumps(list(items)))
        return self
