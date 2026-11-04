"""Los hallazgos de juicio con su disposición humana, y la fuente de `OPEN_FINDING` (`assess`).

Un hallazgo de juicio se identifica por su id estable y sigue abierto hasta que una persona lo
resuelve o lo descarta. Se reúnen los de **todas** las ejecuciones: una ejecución nueva, fallida o
vacía nunca cierra lo que propuso otra. Solo bloquean las propuestas `NC_MAYOR` y `NC_MENOR` sin
disposición que desbloquee (`RESOLVED`, `DISMISSED`); `ACCEPTED` las reconoce y siguen
bloqueando. Los hallazgos deterministas no bloquean aquí: sus hechos ya bloquean por sí mismos.

Una disposición cubre las apariciones del hallazgo hasta la ejecución `covers_run`. Si una
ejecución posterior lo vuelve a proponer, el hallazgo vuelve a estar `OPEN` hasta que una persona
lo disponga de nuevo. El orden entre las dos cadenas lo fija `covers_run`; los instantes deben
confirmarlo: si lo contradicen, es un error visible. `preaudit run` y `preaudit review` escriben
con el lock del ciclo tomado, así que esa contradicción solo puede venir de una manipulación.

Unas cadenas ilegibles, alteradas o con fechas futuras no se interpretan a medias: bloquean con un
error visible.

`judgment_executed` es cierto solo si hay una ejecución con la capa de juicio en `DONE` sobre el
expediente vigente: el último generado antes del instante evaluado, con la misma huella que
revisó la ejecución.
"""

import hashlib
import re
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import datetime

from ens_evidence import chain
from ens_evidence.models.preaudit import (
    BLOCKING_PROPOSALS,
    NEXT_STATES,
    UNBLOCKING,
    Disposition,
    DispositionRecord,
    DispositionState,
    Finding,
    Layer,
    LayerStatus,
    PreAuditRecord,
)
from ens_evidence.models.readiness import OpenFinding
from ens_evidence.preaudit.records import scan_dispositions, scan_runs
from ens_evidence.workspace import NotFound, WorkspaceStore

FINDING_DETAIL = "preaudit_finding"
BROKEN_DETAIL = "preaudit_history_broken"


@dataclass(frozen=True)
class FindingState:
    """Un hallazgo de juicio, la primera ejecución que lo propuso (y su expediente), la última y
    la disposición que cubre esa última (None: abierto)."""

    finding: Finding
    run_seq: int
    dossier: str
    last_run_seq: int
    disposition: Disposition | None = None

    @property
    def state(self) -> DispositionState:
        return self.disposition.state if self.disposition else DispositionState.OPEN

    @property
    def blocks(self) -> bool:
        return self.finding.proposal in BLOCKING_PROPOSALS and self.state not in UNBLOCKING


@dataclass(frozen=True)
class FindingsView:
    states: tuple[FindingState, ...]
    judgment_executed: bool
    problems: tuple[str, ...]

    def state_of(self, finding_id: str) -> FindingState | None:
        return next((s for s in self.states if s.finding.id == finding_id), None)


def findings_view(
    store: WorkspaceStore,
    at: datetime,
    *,
    runs: Collection[str] | None = None,
    dispositions: Collection[str] | None = None,
) -> FindingsView:
    """Los hallazgos de juicio en `at`. `runs` y `dispositions`: las claves congeladas en un
    expediente (None: todas las que hay)."""
    run_scan = scan_runs(store, now=at, known=runs)
    disposition_scan = scan_dispositions(store, now=at, known=dispositions)
    problems = [*run_scan.problems, *disposition_scan.problems]
    proposed = _appearances(run_scan.records)
    decided, decision_problems = _decisions(disposition_scan.records, proposed)
    states = tuple(
        _state(finding, seen, decided.get(finding.id)) for finding, seen in proposed.values()
    )
    executed, sha_problems = _judgment_executed(store, at, run_scan.records)
    problems += decision_problems + sha_problems
    return FindingsView(states, executed, tuple(problems))


type Runs = list[chain.Stored[PreAuditRecord]]


def _appearances(
    records: Iterable[chain.Stored[PreAuditRecord]],
) -> dict[str, tuple[Finding, Runs]]:
    """Cada hallazgo de juicio como lo propuso la primera ejecución, y las ejecuciones que lo
    proponen, en orden de secuencia."""
    proposed: dict[str, tuple[Finding, Runs]] = {}
    for stored in records:
        for finding in stored.record.findings:
            if finding.layer is Layer.JUDGMENT:
                proposed.setdefault(finding.id, (finding, []))[1].append(stored)
    return proposed


def _state(finding: Finding, seen: Runs, decided: Disposition | None) -> FindingState:
    first, last = seen[0].record, seen[-1].record.seq
    covering = decided if decided and decided.covers_run == last else None
    return FindingState(finding, first.seq, first.run.dossier, last, covering)


def _decisions(
    records: Iterable[chain.Stored[DispositionRecord]], proposed: dict[str, tuple[Finding, Runs]]
) -> tuple[dict[str, Disposition], list[str]]:
    """La última disposición válida de cada hallazgo, recorriendo la cadena en orden. Una
    disposición que cubre una ejecución posterior a la anterior parte de `OPEN`."""
    decided: dict[str, Disposition] = {}
    problems: list[str] = []
    for stored in records:
        disposition = stored.record.disposition
        if disposition.finding_id not in proposed:
            problems.append(f"{stored.key}: dispone un hallazgo que ninguna ejecución propuso")
            continue
        order = _order_problems(stored, proposed[disposition.finding_id][1])
        current = decided.get(disposition.finding_id)
        if current and disposition.covers_run < current.covers_run:
            order.append(
                f"{stored.key}: cubre la ejecución {disposition.covers_run}, anterior a la "
                f"{current.covers_run} que ya cubría otra disposición"
            )
        if order:
            problems += order
            continue
        same = current is not None and current.covers_run == disposition.covers_run
        before = current.state if current and same else DispositionState.OPEN
        if disposition.state not in NEXT_STATES[before]:
            problems.append(
                f"{stored.key}: {before.value} → {disposition.state.value} no es una disposición "
                "válida"
            )
            continue
        decided[disposition.finding_id] = disposition
    return decided, problems


def _order_problems(stored: chain.Stored[DispositionRecord], seen: Runs) -> list[str]:
    """`covers_run` debe ser una ejecución que propuso el hallazgo, registrada antes de la
    disposición; y ninguna ejecución posterior en la secuencia puede ser anterior en el reloj."""
    covers, at = stored.record.disposition.covers_run, stored.record.recorded_at
    covered = next((s for s in seen if s.record.seq == covers), None)
    if covered is None:
        return [f"{stored.key}: cubre la ejecución {covers}, que no propuso ese hallazgo"]
    problems = []
    if covered.record.recorded_at > at:
        problems.append(f"{stored.key}: es anterior a la ejecución {covers} que dice cubrir")
    problems += [
        f"{stored.key}: la ejecución {s.record.seq} es anterior en el reloj pero posterior en la "
        f"secuencia a la {covers} que cubre"
        for s in seen
        if s.record.seq > covers and s.record.recorded_at < at
    ]
    return problems


def _dossier_layout() -> tuple[str, re.Pattern[str], str]:
    # Importación diferida: `assessment.dossier` evalúa con `assess`, que usa esta fuente.
    from ens_evidence.assessment.dossier import store as layout

    return layout.ASSESSMENTS_PREFIX, layout.DOSSIER_ID, layout.SNAPSHOT_NAME


def current_dossier(store: WorkspaceStore, at: datetime) -> str | None:
    """El último expediente generado antes de `at` (por el instante de su id)."""
    assessments, dossier_id, _ = _dossier_layout()
    stamp = chain.instant_stamp(at)
    ids = {key.removeprefix(f"{assessments}/").split("/", 1)[0] for key in store.list(assessments)}
    earlier = [d for d in ids if dossier_id.fullmatch(d) and d.split("-", 1)[0] < stamp]
    return max(earlier, default=None)


def _judgment_executed(
    store: WorkspaceStore, at: datetime, records: Iterable[chain.Stored[PreAuditRecord]]
) -> tuple[bool, list[str]]:
    assessments, _, snapshot_name = _dossier_layout()
    dossier = current_dossier(store, at)
    done = [
        s
        for s in records
        if s.record.run.dossier == dossier and s.record.run.judgment_status is LayerStatus.DONE
    ]
    if dossier is None or not done:
        return False, []
    try:
        data = store.read_bytes(f"{assessments}/{dossier}/{snapshot_name}")
    except NotFound:
        return False, [f"{dossier}: no se puede leer el expediente que revisó la pre-auditoría"]
    sha256 = hashlib.sha256(data).hexdigest()
    problems = [
        f"{s.key}: la huella del expediente {dossier} ya no es la que revisó la ejecución"
        for s in done
        if s.record.run.dossier_sha256 != sha256
    ]
    return not problems, problems


def open_findings(view: FindingsView) -> list[OpenFinding]:
    """Lo que bloquea: los hallazgos sin disponer y, si las cadenas no se verifican, un error."""
    found = [
        OpenFinding(
            id=f"{FINDING_DETAIL}:{s.finding.id}",
            description=f"{s.finding.proposal.value} en {s.finding.measure}: {s.finding.reason}",
        )
        for s in view.states
        if s.blocks
    ]
    if view.problems:
        found.append(
            OpenFinding(
                id=f"{BROKEN_DETAIL}:{len(view.problems)}", description="; ".join(view.problems)
            )
        )
    return found


@dataclass(frozen=True)
class PreAuditFindings:
    """`FindingsSource` de `assess`. Sin claves: lo que hay ahora. Con claves: lo que existía al
    generar un expediente (la reproducción ve solo eso)."""

    runs: Collection[str] | None = None
    dispositions: Collection[str] | None = None

    def open_findings(self, store: WorkspaceStore, at: datetime) -> tuple[list[OpenFinding], bool]:
        view = findings_view(store, at, runs=self.runs, dispositions=self.dispositions)
        return open_findings(view), view.judgment_executed and not view.problems
