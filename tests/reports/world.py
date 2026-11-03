"""Instrumentos de los tests del constructor del informe diario."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from ens_evidence.matrix.technical import CheckRun, StaticCheckResults
from ens_evidence.models.contracts import CheckResult
from ens_evidence.models.enums import CheckStatus, CollectionStatus
from ens_evidence.reports.daily import build_daily

MADRID = ZoneInfo("Europe/Madrid")
CUTOFF = datetime(2026, 11, 2, 5, 30, tzinfo=UTC)
PERIOD = "2026-11-02"
ASSETS_LAPTOP = """\
assets:
  - id: lap-ana
    type: laptop
    deployment: deployed
    os: macOS 15
    owner: Ana
    encrypted: true
    usage: trabajo
"""


def daily_at(store, checks, *, generated_at=CUTOFF, period=PERIOD, cycle=None):
    return build_daily(
        store, generated_at=generated_at, period=period, tz=MADRID, cycle=cycle, checks=checks
    )


def check_result(
    status: CheckStatus, *, check_id: str = "github.x", subject: str = "r"
) -> CheckResult:
    return CheckResult(
        check_id=check_id,
        asset_id="gh",
        subject=subject,
        status=status,
        evaluated_at=CUTOFF,
        evidence_ids=["EV-1"] if status is CheckStatus.PASS else [],
        reason="r",
    )


def collection_error_result() -> CheckResult:
    return CheckResult(
        check_id="github.x",
        asset_id="gh",
        status=CheckStatus.ERROR,
        evaluated_at=CUTOFF,
        reason="r",
        collection_status=CollectionStatus.ERROR,
    )


def static_run(
    store, connector: str, seq: int, finished_at: datetime, *results: CheckResult
) -> CheckRun:
    """Un intento cuyo `run_id` existe en el workspace (la fuente se lee por su clave)."""
    run_id = f"evidence/checks/{connector}/{seq:06d}-fake.json"
    store.write_bytes(run_id, f"run {connector} {seq}".encode())
    return CheckRun(
        run_id=run_id,
        connector=connector,
        started_at=finished_at,
        finished_at=finished_at,
        complete=True,
        results=tuple(results),
    )


def source_of(*runs: CheckRun, problems: dict | None = None) -> StaticCheckResults:
    return StaticCheckResults(runs, problems=problems)
