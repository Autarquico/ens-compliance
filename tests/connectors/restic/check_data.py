"""`CollectionResult` mínimos de Restic para probar los checks sin pasar por la recogida."""

from collections.abc import Callable
from datetime import timedelta
from typing import Any

from ens_evidence.connectors.restic.sections import instant
from ens_evidence.models.contracts import CheckResult, CollectionResult

from .fake_restic import NOW, REPOSITORY_ID, SNAPSHOT_ID
from .record_data import OFFSITE, THRESHOLDS

EVIDENCE_ID = "ev-restic-1"
HOUR = timedelta(hours=1)
DAY = timedelta(days=1)

Check = Callable[[CollectionResult, str, Any], list[CheckResult]]


def ago(delta: timedelta = timedelta(0)) -> str:
    return instant(NOW - delta)


def section(items=(), status="SUCCESS", reason=None, **summary):
    return {"status": status, "reason": reason, "items": list(items), "summary": summary}


def repository(*, exists=True, backend="remote"):
    item = {"exists": exists, "repository_id": REPOSITORY_ID if exists else None}
    return section([{**item, "backend": backend}])


def snapshot(age=2 * HOUR, *, run_id="r1", foreign=False, snapshot_id=SNAPSHOT_ID):
    return {"id": snapshot_id, "time": ago(age), "run_id": run_id, "foreign": foreign}


def snapshots(*items, exists=True, **section_args):
    return section(items, repository_exists=exists, **section_args)


def run_item(run_id="r1", status="completed", *, age=2 * HOUR, bound=True, **changes):
    finished = None if status == "started" else ago(age - timedelta(minutes=1))
    item = {
        "run_id": run_id,
        "status": status,
        "started_at": ago(age),
        "finished_at": finished,
        "snapshot_id": SNAPSHOT_ID if status == "completed" else None,
        "has_error": status == "failed",
        "reconstructed_from_snapshot": False,
        "repository_id": REPOSITORY_ID,
        "bound": bound,
    }
    return {**item, **changes}


def check_item(
    check_id="c1", status="completed", *, age=3 * DAY, coverage=10, bound=True, **changes
):
    finished = None if status == "started" else ago(age - timedelta(minutes=1))
    measured = status != "started"
    item = {
        "check_id": check_id,
        "status": status,
        "started_at": ago(age),
        "finished_at": finished,
        "scope": "metadata" if coverage == 0 else f"subset:{coverage}%",
        "read_data_percent": coverage,
        "exit_code": (0 if status == "completed" else 1) if measured else None,
        "num_errors": (0 if status == "completed" else 2) if measured else None,
        "broken_packs": 0 if status == "completed" else None,
        "failure": "repository_errors" if status == "failed" else None,
        "repository_id": REPOSITORY_ID,
        "bound": bound,
    }
    return {**item, **changes}


def restore_test_item(
    test_id="t1",
    *,
    age=10 * DAY,
    valid_for=365 * DAY,
    bound=True,
    linked=True,
    result=None,
    review=None,
    **changes,
):
    performed = NOW - age
    reviewed = performed + HOUR
    item = {
        "test_id": test_id,
        "system": "bk-diario",
        "performed_at": instant(performed),
        "restore_id": "rs1",
        "isolated_target": True,
        "backup": {"snapshot_id": SNAPSHOT_ID},
        "result": {
            "restored": True,
            "integrity_verified": True,
            "application_started": True,
            "application_justification": None,
            **(result or {}),
        },
        "review": {
            "approved": True,
            "reviewer": "Ana",
            "role": "security_officer",
            "reviewed_at": instant(reviewed),
            "valid_until": instant(reviewed + valid_for),
            **(review or {}),
        },
        "repository_id": REPOSITORY_ID,
        "restore_linked": linked,
        "bound": bound,
    }
    return {**item, **changes}


def healthy_sections() -> dict[str, dict]:
    return {
        "repository": repository(),
        "snapshots": snapshots(snapshot()),
        "runs": section([run_item()], unreadable=[]),
        "integrity_checks": section([check_item()], unreadable=[]),
        "restore_tests": section([restore_test_item()], unreadable=[]),
    }


def evidence(
    *,
    thresholds=None,
    offsite=OFFSITE,
    repository_id=REPOSITORY_ID,
    collection_status="SUCCESS",
    error=None,
    **sections,
) -> CollectionResult:
    """Recogida sana; cada sección dada por nombre sustituye a la de por defecto."""
    return CollectionResult(
        source="restic",
        collector="restic",
        collected_at=NOW,
        asset_id="bk-diario",
        collection_status=collection_status,
        data={
            "error": error,
            "repository_id": repository_id,
            "thresholds": {**THRESHOLDS, **(thresholds or {})},
            "offsite": offsite,
            "sections": {**healthy_sections(), **sections},
        },
    )


def run(check: Check, collected: CollectionResult, at=NOW) -> CheckResult:
    """Evalúa un check, valida cada `CheckResult` contra el modelo y exige que cite la evidencia."""
    results = check(collected, EVIDENCE_ID, at)
    assert len(results) == 1, [(r.subject, r.status, r.reason) for r in results]
    (result,) = results
    CheckResult.model_validate(result.model_dump())
    assert result.evidence_ids == [EVIDENCE_ID]
    return result
