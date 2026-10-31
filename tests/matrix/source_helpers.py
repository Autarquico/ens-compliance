from datetime import UTC, datetime, timedelta

from ens_evidence.evidence.importer import import_collected, import_manual
from ens_evidence.evidence.manifests import ManifestError, load_manifest_by_id
from ens_evidence.matrix import (
    CheckLink,
    CheckRun,
    ClauseRef,
    ManualEvidenceLink,
    MatrixRowSpec,
    Obligation,
    RowKind,
)
from ens_evidence.matrix.evidence import manifest_sha256
from ens_evidence.models.contracts import CheckResult, CollectionResult
from ens_evidence.models.enums import CheckStatus, CollectionStatus

AT = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
CHECK = "github.default_branch_protected"
OTHER_CHECK = "restic.backup_001_recent"
SHA = "c" * 64

ROW = MatrixRowSpec(
    "org.1",
    RowKind.SELECTED,
    (
        Obligation(
            "base",
            (ClauseRef("org.1.1", "a", "texto"), ClauseRef("org.1.2", "b", "texto")),
        ),
    ),
    SHA,
)


def manual_evidence(store, *, collected_at=None, valid_until=None, now=None, data=b"politica"):
    now = now or AT - timedelta(days=1)
    return import_manual(
        store,
        data,
        asset_id="svc-facturacion",
        submitted_by="Ana",
        now=now,
        collected_at=collected_at,
        valid_until=valid_until,
    ).id


_STORE_OF: dict[str, object] = {}  # id de evidencia -> almacén donde la creó `collected_evidence`


def fingerprints(evidence_ids) -> dict[str, str]:
    """La huella que registraría el motor al ejecutar: la del manifiesto tal como está ahora."""
    found = {}
    for evidence_id in evidence_ids:
        store = _STORE_OF.get(evidence_id)
        try:
            if store is not None:
                found[evidence_id] = manifest_sha256(load_manifest_by_id(store, evidence_id))
        except ManifestError:
            continue
    return found


def collected_evidence(store, *, source="github", asset="repo-a", collected_at=None, now=None):
    now = now or AT - timedelta(days=1)
    result = CollectionResult(
        source=source,
        collector=source,
        collected_at=collected_at or now,
        asset_id=asset,
        data={"asset": asset},
        collection_status=CollectionStatus.SUCCESS,
    )
    evidence_id = import_collected(store, result, collector_version="1", now=now).id
    _STORE_OF[evidence_id] = store
    return evidence_id


def check_result(
    store,
    subject="repo-a",
    *,
    check_id=CHECK,
    status=CheckStatus.PASS,
    evaluated_at=None,
    collected_at=None,
    evidence_ids=None,
) -> CheckResult:
    evaluated_at = evaluated_at or AT - timedelta(days=1)
    if evidence_ids is None:
        evidence_ids = [collected_evidence(store, asset=subject, collected_at=collected_at)]
    return CheckResult(
        check_id=check_id,
        asset_id=subject,
        status=status,
        evaluated_at=evaluated_at,
        evidence_ids=evidence_ids,
        reason="r",
    )


def run(
    results,
    *,
    run_id="run-1",
    connector="github",
    finished_at=None,
    complete=True,
    expected=("repo-a",),
    errors=(),
    expected_by_check=None,
    evidence_sha256=None,
) -> CheckRun:
    """`evidence_sha256`: por defecto la huella actual de cada evidencia citada, como si el motor
    la hubiera registrado al construir la ejecución; `{}` simula una ejecución sin huellas."""
    finished_at = finished_at or AT - timedelta(hours=12)
    results = tuple(results)
    if evidence_sha256 is None:
        evidence_sha256 = fingerprints(i for r in results for i in r.evidence_ids)
    return CheckRun(
        run_id=run_id,
        connector=connector,
        started_at=finished_at - timedelta(minutes=5),
        finished_at=finished_at,
        complete=complete,
        expected_subjects=frozenset(expected),
        results=results,
        errors=tuple(errors),
        expected_by_check={k: frozenset(v) for k, v in (expected_by_check or {}).items()},
        evidence_sha256=evidence_sha256,
    )


def check_link(check_id=CHECK, covers=("base",)) -> CheckLink:
    return CheckLink(check_id=check_id, covers=list(covers))


def manual_link(evidence_id, covers=("base",)) -> ManualEvidenceLink:
    return ManualEvidenceLink(evidence_id=evidence_id, covers=list(covers))
