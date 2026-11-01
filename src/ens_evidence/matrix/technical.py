"""Evidencia técnica: contrato de ejecución de checks y agregación conservadora.

Esta spec define el contrato (`CheckRun`) y la agregación; la fuente real la aporta checks-engine.
Sin fuente `NoCheckResults` no sustenta nada. Por conector cuenta el último intento en o antes de
`at`, completo o no: si no es utilizable el conector bloquea y nunca se vuelve a un PASS anterior.
Los conectores son independientes.

Un sujeto es `CheckResult.subject` o, si no lo tiene, `CheckResult.asset_id`.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import Protocol

from ens_evidence.checks.catalog import KNOWN_CHECKS
from ens_evidence.matrix.evidence import (
    LinkSupport,
    blocker,
    link_units,
    manifest_sha256,
    verified_manifest,
)
from ens_evidence.matrix.model import CheckLink
from ens_evidence.matrix.obligations import MatrixRowSpec
from ens_evidence.models.contracts import CheckResult
from ens_evidence.models.enums import CheckStatus
from ens_evidence.models.readiness import Blocker, BlockerKind, EvidenceRef
from ens_evidence.workspace import WorkspaceStore

TECHNICAL_MAX_AGE = timedelta(days=30)


def technical_valid_until(collected_at: datetime, valid_until: datetime | None) -> datetime:
    """La evidencia técnica nunca vale más de 30 días desde su recogida (D2): alargar el
    `valid_until` del manifiesto no reactiva un PASS antiguo."""
    limit = collected_at + TECHNICAL_MAX_AGE
    return limit if valid_until is None else min(valid_until, limit)


@dataclass(frozen=True)
class CheckRun:
    """Una ejecución de los checks de un conector.

    `expected_subjects`: sujetos que debía cubrir (activos del alcance de ese conector);
    `expected_by_check` los precisa por check cuando difieren (p. ej. repos archivados).
    `complete`: terminó y cubrió todo; un intento incompleto no sustenta nada.
    `evidence_sha256`: huella (`manifest_sha256`) de cada evidencia citada, tomada al ejecutar.
    """

    run_id: str
    connector: str
    started_at: datetime
    finished_at: datetime
    complete: bool
    expected_subjects: frozenset[str] = frozenset()
    results: tuple[CheckResult, ...] = ()
    errors: tuple[str, ...] = field(default=())
    expected_by_check: Mapping[str, frozenset[str]] = field(default_factory=dict)
    evidence_sha256: Mapping[str, str] = field(default_factory=dict)

    def expected_for(self, check_id: str) -> frozenset[str]:
        return self.expected_by_check.get(check_id, self.expected_subjects)

    def __post_init__(self) -> None:
        if self.finished_at < self.started_at:
            raise ValueError(f"{self.run_id}: finished_at anterior a started_at")
        if self.complete and self.errors:
            raise ValueError(f"{self.run_id}: una ejecución con errores no puede estar completa")


ConnectorProblems = Mapping[str, Sequence[str]]


class CheckResultsSource(Protocol):
    def runs(self, store: WorkspaceStore, at: datetime) -> Sequence[CheckRun]: ...

    def connector_problems(self, store: WorkspaceStore, at: datetime) -> ConnectorProblems:
        """Conector -> detalles de bloqueo (historial ilegible, alcance cambiado...): mientras
        haya alguno, ningún check de ese conector sustenta nada."""
        ...


class StaticCheckResults:
    def __init__(
        self, runs: Sequence[CheckRun] = (), *, problems: ConnectorProblems | None = None
    ) -> None:
        self._runs = tuple(runs)
        self._problems = dict(problems or {})

    def runs(self, store: WorkspaceStore, at: datetime) -> Sequence[CheckRun]:
        return self._runs

    def connector_problems(self, store: WorkspaceStore, at: datetime) -> ConnectorProblems:
        return self._problems


class NoCheckResults(StaticCheckResults):
    """Defecto sin fuente real: ningún check cuenta."""

    def __init__(self, *, problems: ConnectorProblems | None = None) -> None:
        super().__init__((), problems=problems)


def load_checks(
    source: CheckResultsSource | None, store: WorkspaceStore, at: datetime
) -> tuple[tuple[CheckRun, ...], ConnectorProblems]:
    """Lo que una agregación necesita de la fuente, leído una sola vez."""
    source = source or NoCheckResults()
    return tuple(source.runs(store, at)), source.connector_problems(store, at)


def problem_errors(problems: ConnectorProblems) -> list[str]:
    return [
        f"checks {connector}: {d}"
        for connector, details in sorted(problems.items())
        for d in details
    ]


def subject_of(result: CheckResult) -> str:
    return result.subject or result.asset_id


def latest_run(runs: Sequence[CheckRun], connector: str, at: datetime) -> CheckRun | None:
    """El último intento del conector en o antes de `at`, completo o no."""
    attempts = [r for r in runs if r.connector == connector and r.finished_at <= at]
    return max(attempts, key=lambda r: (r.finished_at, r.started_at, r.run_id), default=None)


_SEVERITY = {
    CheckStatus.PASS: 0,
    CheckStatus.UNKNOWN: 1,
    CheckStatus.ERROR: 2,
    CheckStatus.FAIL: 3,
}


def _results_by_subject(run: CheckRun, check_id: str, at: datetime) -> dict[str, CheckResult]:
    """Por sujeto, el resultado más reciente (el peor si empatan); los futuros se ignoran."""
    chosen: dict[str, CheckResult] = {}
    for result in run.results:
        if result.check_id != check_id or result.evaluated_at > at:
            continue
        subject = subject_of(result)
        current = chosen.get(subject)
        if current is None or (result.evaluated_at, _SEVERITY[result.status]) > (
            current.evaluated_at,
            _SEVERITY[current.status],
        ):
            chosen[subject] = result
    return chosen


def _pass_evidence_blockers(
    store: WorkspaceStore, row: MatrixRowSpec, result: CheckResult, run: CheckRun, at: datetime
) -> tuple[list[Blocker], list[EvidenceRef]]:
    blockers: list[Blocker] = []
    refs: list[EvidenceRef] = []
    for evidence_id in result.evidence_ids:
        manifest = verified_manifest(store, evidence_id)
        if manifest is None:
            blockers.append(
                blocker(BlockerKind.MISSING_EVIDENCE, row, f"evidence_unverified:{evidence_id}")
            )
        elif (recorded := run.evidence_sha256.get(evidence_id)) is None:
            blockers.append(
                blocker(BlockerKind.MISSING_EVIDENCE, row, f"evidence_unbound:{evidence_id}")
            )
        elif recorded != manifest_sha256(manifest):
            blockers.append(
                blocker(BlockerKind.MISSING_EVIDENCE, row, f"check_evidence_changed:{evidence_id}")
            )
        elif manifest.collected_at > at:
            blockers.append(
                blocker(BlockerKind.MISSING_EVIDENCE, row, f"evidence_in_future:{evidence_id}")
            )
        elif manifest.collected_at > min(result.evaluated_at, run.finished_at):
            # La evidencia es anterior al resultado que la cita: si no, o se ha editado su
            # fecha para rejuvenecerla o no es la que el check vio. Hace falta una ejecución nueva.
            blockers.append(
                blocker(BlockerKind.MISSING_EVIDENCE, row, f"evidence_inconsistent:{evidence_id}")
            )
        elif technical_valid_until(manifest.collected_at, manifest.valid_until) <= at:
            blockers.append(
                blocker(BlockerKind.EXPIRED_EVIDENCE, row, f"evidence_expired:{evidence_id}")
            )
        else:
            refs.append(
                EvidenceRef(
                    id=evidence_id,
                    valid_until=technical_valid_until(manifest.collected_at, manifest.valid_until),
                    collected_at=manifest.collected_at,
                )
            )
    return blockers, refs


def _result_blockers(
    store: WorkspaceStore, row: MatrixRowSpec, result: CheckResult, run: CheckRun, at: datetime
) -> tuple[list[Blocker], list[EvidenceRef]]:
    where = f"{result.check_id}:{subject_of(result)}"
    match result.status:
        case CheckStatus.FAIL:
            return [blocker(BlockerKind.NOT_COMPLIANT, row, f"check_failed:{where}")], []
        case CheckStatus.ERROR:
            return [blocker(BlockerKind.MISSING_EVIDENCE, row, f"check_error:{where}")], []
        case CheckStatus.UNKNOWN:
            return [
                blocker(BlockerKind.MISSING_EVIDENCE, row, f"check_unknown_subject:{where}")
            ], []
        case CheckStatus.PASS:
            return _pass_evidence_blockers(store, row, result, run, at)


def technical_support(
    store: WorkspaceStore,
    row: MatrixRowSpec,
    link: CheckLink,
    runs: Sequence[CheckRun],
    at: datetime,
    problems: ConnectorProblems = MappingProxyType({}),
) -> LinkSupport:
    """Qué sustenta un check vinculado en `at`: solo si todos los sujetos esperados están en PASS
    con evidencia íntegra y vigente."""
    units, blockers = link_units(row, link)
    check_id = link.check_id

    def rejected(*details: str) -> LinkSupport:
        found = [blocker(BlockerKind.MISSING_EVIDENCE, row, d) for d in details]
        return LinkSupport(link, blockers=(*blockers, *found))

    connector = KNOWN_CHECKS.get(check_id)
    if connector is None:
        return rejected(f"check_unknown:{check_id}")
    if connector_problems := problems.get(connector):
        return rejected(*connector_problems)
    run = latest_run(runs, connector, at)
    if run is None:
        return rejected(f"check_no_results:{check_id}")
    if not run.complete:
        return rejected(f"check_last_run_failed:{connector}")
    results = _results_by_subject(run, check_id, at)
    missing = sorted(run.expected_for(check_id) - results.keys())
    if not results and not missing:
        return rejected(f"check_no_results:{check_id}")

    found = list(blockers)
    found += [
        blocker(BlockerKind.MISSING_EVIDENCE, row, f"check_incomplete:{check_id}:{s}")
        for s in missing
    ]
    refs: dict[str, EvidenceRef] = {}
    for subject in sorted(results):
        result_blockers, result_refs = _result_blockers(store, row, results[subject], run, at)
        found += result_blockers
        refs.update((ref.id, ref) for ref in result_refs)
    unique = tuple({(b.kind, b.detail): b for b in found}.values())
    return LinkSupport(
        link,
        clauses=units if not unique else frozenset(),
        blockers=unique,
        evidence=tuple(refs.values()),
        available_at=run.finished_at,
    )
