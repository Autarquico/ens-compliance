"""Checks BACKUP-001, 003, 004, 005 y 006 de `backups`: la lógica de Restic, por repositorio.

Cada repositorio de `data.repositories` se proyecta a un `CollectionResult` con la forma que
esperan los checks de Restic y se evalúa con ellos; el resultado lleva el `asset_id` del
repositorio. Solo BACKUP-001 difiere: cuenta cualquier snapshot, porque estos repositorios los
escribe otra herramienta y no hay runs del workspace que los citen. BACKUP-002 (errores de
ejecución) no aplica: la app no ve las ejecuciones de Backrest; una copia fallida se detecta
por BACKUP-001 (frescura). BACKUP-004 evalúa el `restic check` que el conector ejecuta en
cada recogida. Sin repositorios
interpretables no hay PASS.
"""

from collections.abc import Callable
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from ens_evidence.connectors.restic import checks as restic
from ens_evidence.models.contracts import CheckResult, CollectionResult
from ens_evidence.models.enums import CheckStatus, CollectionStatus

RECENT = "backups.backup_001_recent"
REPOSITORY_ACCESSIBLE = "backups.backup_003_repository_accessible"
INTEGRITY = "backups.backup_004_integrity"
RESTORE_TEST = "backups.backup_005_restore_test"
OFFSITE = "backups.backup_006_offsite"

Check = Callable[[CollectionResult, str, datetime], list[CheckResult]]


def _views(result: CollectionResult) -> list[CollectionResult] | None:
    entries = result.data.get("repositories")
    if not isinstance(entries, list) or not entries:
        return None
    try:
        return [_view(result, entry) for entry in entries]
    except (ValidationError, TypeError, KeyError, ValueError):
        return None


_VIEW_KEYS = ("error", "repository_id", "thresholds", "offsite", "sections")


def _view(result: CollectionResult, entry: dict[str, Any]) -> CollectionResult:
    return CollectionResult(
        source=result.source,
        collector=result.collector,
        collected_at=result.collected_at,
        asset_id=entry["asset_id"],
        data={key: entry.get(key) for key in _VIEW_KEYS},
        collection_status=CollectionStatus(entry["collection_status"]),
    )


def _unreadable(check_id: str, result: CollectionResult, evaluated_at: datetime, evidence_id: str):
    return [
        CheckResult(
            check_id=check_id,
            asset_id=result.asset_id,
            status=CheckStatus.UNKNOWN,
            evaluated_at=evaluated_at,
            evidence_ids=[evidence_id],
            reason="la evidencia no lista repositorios interpretables",
        )
    ]


def _per_repository(check_id: str, check: Check) -> Check:
    def run(result: CollectionResult, evidence_id: str, evaluated_at: datetime):
        views = _views(result)
        if views is None:
            return _unreadable(check_id, result, evaluated_at, evidence_id)
        return [
            outcome.model_copy(update={"check_id": check_id})
            for view in views
            for outcome in check(view, evidence_id, evaluated_at)
        ]

    return run


def _recent(view: CollectionResult, evidence_id: str, evaluated_at: datetime) -> list[CheckResult]:
    return restic.evaluate(
        RECENT,
        view,
        evidence_id,
        evaluated_at,
        restic.repository_id(view),
        restic.recent_any_snapshot,
    )


backup_001_recent = _per_repository(RECENT, _recent)
backup_003_repository_accessible = _per_repository(
    REPOSITORY_ACCESSIBLE, restic.backup_003_repository_accessible
)
backup_004_integrity = _per_repository(INTEGRITY, restic.backup_004_integrity)
backup_005_restore_test = _per_repository(RESTORE_TEST, restic.backup_005_restore_test)
backup_006_offsite = _per_repository(OFFSITE, restic.backup_006_offsite)

ALL_CHECKS = (
    backup_001_recent,
    backup_003_repository_accessible,
    backup_004_integrity,
    backup_005_restore_test,
    backup_006_offsite,
)
