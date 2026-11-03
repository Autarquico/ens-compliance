"""Historial de checks congelado al generar: la vista que reproduce una instantánea.

La instantánea guarda las claves de las ejecuciones que existían al generarla. Reproducir ve
exactamente esas: una ejecución registrada después (un `check --all` rutinario) no existía y no
cuenta, y una que ya existía con fecha futura sigue bloqueando como bloqueó entonces.
"""

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime

from ens_evidence.checks.source import StoredCheckResults
from ens_evidence.checks.store import scan_runs
from ens_evidence.matrix.technical import CheckRun, ConnectorProblems
from ens_evidence.workspace import NotFound, WorkspaceStore


def history_keys(store: WorkspaceStore) -> list[str]:
    """Las claves de todas las ejecuciones legibles del historial, ordenadas."""
    scan = scan_runs(store)
    return sorted(s.key for found in scan.runs.values() for s in found)


class _WithoutLaterRuns:
    """El almacén sin las ejecuciones legibles que no estaban en `known`."""

    def __init__(self, store: WorkspaceStore, known: Collection[str]) -> None:
        scan = scan_runs(store)
        known_keys = set(known)
        self._store = store
        self._hidden = {
            s.key for found in scan.runs.values() for s in found if s.key not in known_keys
        }

    def list(self, prefix: str = "") -> list[str]:
        return [key for key in self._store.list(prefix) if key not in self._hidden]

    def exists(self, key: str) -> bool:
        return key not in self._hidden and self._store.exists(key)

    def read_text(self, key: str) -> str:
        self._require_visible(key)
        return self._store.read_text(key)

    def read_bytes(self, key: str) -> bytes:
        self._require_visible(key)
        return self._store.read_bytes(key)

    def _require_visible(self, key: str) -> None:
        if key in self._hidden:
            raise NotFound("clave no encontrada")

    def write_text(self, key: str, text: str) -> None:
        raise TypeError("la vista congelada del historial no escribe")

    def write_bytes(self, key: str, data: bytes) -> None:
        raise TypeError("la vista congelada del historial no escribe")

    def create_bytes(self, key: str, data: bytes) -> None:
        raise TypeError("la vista congelada del historial no escribe")


@dataclass(frozen=True)
class FrozenCheckResults:
    reference_now: datetime
    run_keys: Collection[str]

    def _live(self) -> StoredCheckResults:
        return StoredCheckResults(reference_now=self.reference_now)

    def runs(self, store: WorkspaceStore, at: datetime) -> Sequence[CheckRun]:
        return self._live().runs(_WithoutLaterRuns(store, self.run_keys), at)

    def connector_problems(self, store: WorkspaceStore, at: datetime) -> ConnectorProblems:
        return self._live().connector_problems(_WithoutLaterRuns(store, self.run_keys), at)
