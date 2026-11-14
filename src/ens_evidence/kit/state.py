"""Juicio del estado de una herramienta del kit a partir de la última evidencia guardada.

Sin UI ni red: lo comparten la vista de herramientas y el monitor, para que no diverjan.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from ens_evidence.checks.store import scan_runs
from ens_evidence.connectors.keystore.checks import SEALED_CODE, UNSEALED
from ens_evidence.models.enums import CheckStatus
from ens_evidence.workspace import WorkspaceStore

KEYSTORE_CONNECTOR = "keystore"
KitStateKey = Literal["ok", "problems", "down", "nodata", "sealed"]
_UNANSWERED = {CheckStatus.ERROR, CheckStatus.UNKNOWN}


@dataclass(frozen=True)
class KitJudgment:
    key: KitStateKey
    checked_at: datetime | None
    """Fin de la última ejecución; `None` si no hay ninguna (o es ilegible)."""
    has_checks: bool = True
    """`False` si hay ejecución pero ninguno de sus checks es de la herramienta."""


def judge_kit(
    store: WorkspaceStore, connector: str, check_ids: tuple[str, ...] = ()
) -> KitJudgment:
    """Con `check_ids` solo juzga esos checks: el conector puede servir a más medidas."""
    scan = scan_runs(store, connector)
    last = scan.last(connector)
    if last is None:
        return KitJudgment("problems" if connector in scan.unreadable else "nodata", None)
    run = last.run
    if connector == KEYSTORE_CONNECTOR and any(
        r.check_id == UNSEALED and r.status is CheckStatus.FAIL and r.reason_code == SEALED_CODE
        for r in run.results
    ):
        return KitJudgment("sealed", run.finished_at)
    results = [r for r in run.results if not check_ids or r.check_id in check_ids]
    if check_ids and not results:
        return KitJudgment("nodata", run.finished_at, has_checks=False)
    if not run.complete or all(r.status in _UNANSWERED for r in results):
        return KitJudgment("down", run.finished_at)
    healthy = all(r.status is CheckStatus.PASS for r in results)
    return KitJudgment("ok" if healthy else "problems", run.finished_at)
