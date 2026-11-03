"""Historial de checks congelado en `reference_now`: la vista que reproduce una instantánea.

Una ejecución que termina después del instante de referencia no existía cuando se generó el
expediente: ni cuenta ni es «ejecución en el futuro». Sin esto, un `check --all` rutinario haría
que el expediente ya no se pudiera reproducir. Las ejecuciones anteriores se tratan como siempre.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from ens_evidence.checks.source import StoredCheckResults
from ens_evidence.checks.store import scan_runs
from ens_evidence.matrix.technical import CheckRun, ConnectorProblems
from ens_evidence.workspace import WorkspaceStore


@dataclass(frozen=True)
class FrozenCheckResults:
    reference_now: datetime

    def runs(self, store: WorkspaceStore, at: datetime) -> Sequence[CheckRun]:
        live = StoredCheckResults(reference_now=self.reference_now).runs(store, at)
        return [run for run in live if run.finished_at <= self.reference_now]

    def connector_problems(self, store: WorkspaceStore, at: datetime) -> ConnectorProblems:
        scan = scan_runs(store)
        later = [
            stored.run.finished_at
            for connector in scan.runs
            for stored in scan.live(connector)
            if stored.run.finished_at > self.reference_now
        ]
        # Con el reloj en la última ejecución posterior, ninguna cuenta como «en el futuro».
        clock = max(later, default=self.reference_now)
        return StoredCheckResults(reference_now=clock).connector_problems(store, at)
