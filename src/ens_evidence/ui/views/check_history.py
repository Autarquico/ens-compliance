"""Lo que dicen los últimos resultados de checks de un activo, sin darlo por bueno de más.

Compartido por el inventario y por `/copias`: la última ejecución vigente de cada conector, su
cobertura y el detalle por check. Nunca expone `CheckResult.reason`: puede citar valores.
"""

from collections import defaultdict
from dataclasses import dataclass
from typing import NamedTuple

from ens_evidence.checks.model import StoredCheckRun
from ens_evidence.checks.store import RunScan
from ens_evidence.models.contracts import CheckResult
from ens_evidence.models.enums import CheckStatus, CollectionStatus
from ens_evidence.ui.pages import status_badge
from ens_evidence.ui.views import format_instant

_WORST_FIRST = (CheckStatus.FAIL, CheckStatus.ERROR, CheckStatus.UNKNOWN, CheckStatus.PASS)
_RANK = {status: rank for rank, status in enumerate(_WORST_FIRST)}


@dataclass(frozen=True)
class EvidenceSummary:
    """Lo que dicen los últimos resultados del activo, sin darlo por bueno más de lo que consta."""

    status: str  # el peor: FAIL > ERROR > UNKNOWN > PASS
    label: str  # texto llano del estado (con «parcial» si un PASS no tiene cobertura completa)
    favorable: bool  # solo un PASS con cobertura completa; nunca implica conformidad
    coverage: str  # "completa" | "parcial"
    partial_reasons: tuple[str, ...]
    connector: str  # los conectores de los que salen los resultados, separados por coma
    finished_at: str  # la ejecución más reciente
    checks: int
    evidence_ids: tuple[str, ...]
    sentence: str  # «Último resultado de N checks de <conector> el <fecha>»

    @property
    def tone(self) -> str:
        return status_badge(self.status, self.status == "PASS" and not self.favorable)[0]


@dataclass(frozen=True)
class CheckRow:
    check_id: str
    status: str
    collection_status: str
    reason_code: str | None
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class ConnectorResults:
    connector: str
    finished_at: str
    complete: bool
    rows: tuple[CheckRow, ...]


class Contribution(NamedTuple):
    """La ejecución más reciente de un conector con resultados del activo."""

    run: StoredCheckRun
    results: list[CheckResult]
    superseded: bool  # una ejecución posterior del conector ya no incluyó el activo


def latest_results(scan: RunScan) -> dict[str, list[Contribution]]:
    """Por activo y conector legible, la última ejecución vigente que trae resultados suyos."""
    found: dict[str, list[Contribution]] = defaultdict(list)
    for connector in sorted(scan.runs):
        live = scan.live(connector)
        newest = len(live) - 1
        seen: set[str] = set()
        for position in range(newest, -1, -1):
            run = live[position].run
            mine: dict[str, list[CheckResult]] = defaultdict(list)
            for result in run.results:
                mine[result.asset_id].append(result)
            for asset_id, results in mine.items():
                if asset_id not in seen:
                    seen.add(asset_id)
                    found[asset_id].append(Contribution(run, results, position != newest))
    return found


def unreadable_notes(scan: RunScan) -> tuple[str, ...]:
    return tuple(
        f"El historial de checks de {connector or 'un conector sin identificar'} no se ha podido "
        "leer: no se muestran sus resultados."
        for connector in sorted(scan.unreadable)
    )


def partial_reasons(scan: RunScan, contributions: list[Contribution]) -> tuple[str, ...]:
    reasons = [
        "No se ha podido leer el historial de checks de "
        f"{connector or 'un conector sin identificar'}."
        for connector in sorted(scan.unreadable)
    ]
    for run, results, superseded in contributions:
        if superseded:
            reasons.append(
                f"Una ejecución posterior de {run.connector} no incluyó este activo: "
                "el resultado puede estar desactualizado."
            )
        if not run.complete:
            reasons.append(f"La última ejecución de {run.connector} quedó incompleta.")
        if any(r.collection_status is not CollectionStatus.SUCCESS for r in results):
            reasons.append(f"En {run.connector}, algún check no pudo recoger sus datos.")
    return tuple(dict.fromkeys(reasons))


def _sentence(contributions: list[Contribution]) -> str:
    parts = [
        f"{len(results)} {'check' if len(results) == 1 else 'checks'} de {run.connector} "
        f"el {format_instant(run.finished_at)}"
        for run, results, _ in contributions
    ]
    return "Último resultado de " + "; ".join(parts)


def summary(scan: RunScan, contributions: list[Contribution]) -> EvidenceSummary:
    results = [r for _, rs, _ in contributions for r in rs]
    worst = min((r.status for r in results), key=_RANK.__getitem__)
    reasons = partial_reasons(scan, contributions)
    coverage = "parcial" if reasons else "completa"
    _, label = status_badge(worst.value, worst is CheckStatus.PASS and bool(reasons))
    return EvidenceSummary(
        status=worst.value,
        label=label,
        favorable=worst is CheckStatus.PASS and not reasons,
        coverage=coverage,
        partial_reasons=reasons,
        connector=", ".join(run.connector for run, _, _ in contributions),
        finished_at=format_instant(max(run.finished_at for run, _, _ in contributions)),
        checks=len(results),
        evidence_ids=tuple(sorted({e for r in results for e in r.evidence_ids})),
        sentence=_sentence(contributions),
    )


def detail_results(contributions: list[Contribution]) -> tuple[ConnectorResults, ...]:
    return tuple(
        ConnectorResults(
            connector=run.connector,
            finished_at=format_instant(run.finished_at),
            complete=run.complete,
            rows=tuple(
                CheckRow(
                    r.check_id,
                    r.status.value,
                    r.collection_status.value,
                    r.reason_code,
                    tuple(r.evidence_ids),
                )  # nunca `reason`: puede citar valores de las filas
                for r in sorted(results, key=lambda r: r.check_id)
            ),
        )
        for run, results, _ in contributions
    )
