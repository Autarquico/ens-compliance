"""Deriva: ¿el workspace sigue describiendo lo que el expediente registró?

Se reconstruye la instantánea con las entradas congeladas y se compara la proyección de deriva
(`build.drift_projection`). Una diferencia se informa por sección. Si ni siquiera se puede
reconstruir (la categorización ya no es vigente, cambió la versión del producto, aparece un
secreto), eso también es deriva: el expediente ya no se reproduce.
"""

from dataclasses import dataclass

from ens_evidence.assessment.dossier.build import DossierRejected, build_snapshot, diff_projection
from ens_evidence.assessment.dossier.model import DossierSnapshot
from ens_evidence.categorization import CategorizationNotCurrent
from ens_evidence.matrix import CheckResultsSource
from ens_evidence.workspace import WorkspaceStore

REBUILD = "Reconstrucción"


@dataclass(frozen=True)
class DriftReport:
    """`drift`: líneas por sección (vacío si el expediente sigue vigente)."""

    drift: dict[str, list[str]]

    @property
    def current(self) -> bool:
        return not self.drift


def rebuild_and_diff(
    store: WorkspaceStore, snapshot: DossierSnapshot, *, checks: CheckResultsSource | None = None
) -> DriftReport:
    """Reconstruye con `snapshot.inputs` y compara; el paso del tiempo no deriva, porque la
    evaluación se repite en el instante congelado."""
    try:
        rebuilt = build_snapshot(store, snapshot.inputs, checks=checks)
    except DossierRejected as exc:
        return DriftReport({REBUILD: [*exc.errors]})
    except CategorizationNotCurrent as exc:
        return DriftReport({REBUILD: [f"la categorización ya no está vigente ({exc})"]})
    return DriftReport(diff_projection(snapshot, rebuilt))
