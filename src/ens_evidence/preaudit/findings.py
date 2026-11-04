"""Los hallazgos de juicio con su disposición humana, y la fuente de `OPEN_FINDING` (`assess`).

Un hallazgo de juicio se identifica por su id estable y sigue abierto hasta que una persona lo
resuelve o lo descarta. Se reúnen los de **todas** las ejecuciones: una ejecución nueva, fallida o
vacía nunca cierra lo que propuso otra. Solo bloquean las propuestas `NC_MAYOR` y `NC_MENOR` sin
disposición que desbloquee (`RESOLVED`, `DISMISSED`); `ACCEPTED` las reconoce y siguen
bloqueando. Los hallazgos deterministas no bloquean aquí: sus hechos ya bloquean por sí mismos.

Una disposición cubre las apariciones del hallazgo hasta la ejecución `covers_run`. Si una
ejecución posterior lo vuelve a proponer, el hallazgo vuelve a estar `OPEN` hasta que una persona
lo disponga de nuevo. Entre las apariciones que no cubre ninguna disposición que desbloquea manda
la propuesta más grave: una NC nunca deja de bloquear porque antes fuera una observación.

El orden entre las dos cadenas lo fija `covers_run`; los instantes deben confirmarlo: si lo
contradicen, es un error visible. `preaudit run` y `preaudit review` escriben con el lock del
ciclo tomado, así que esa contradicción solo puede venir de una manipulación.

Una `RESOLVED` se vuelve a comprobar al leer, no solo al registrarla: su evidencia correctora
tiene que seguir existiendo e íntegra, recogida después de la ejecución que cubre y distinta de
las que cita el hallazgo (`correction_problems`). Si se borra o se altera, la historia está rota.

Unas cadenas ilegibles, alteradas o con fechas futuras no se interpretan a medias: bloquean con un
error visible. También bloquea que falte algo que congeló un expediente (`frozen_problems`).

`judgment_executed` es cierto solo si hay una ejecución con la capa de juicio en `DONE` sobre el
expediente vigente: el último generado antes del instante evaluado, con la misma huella que
revisó la ejecución.
"""

import hashlib
import json
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import datetime

from ens_evidence import chain
from ens_evidence.dossier_layout import dossiers_before, matches_content, snapshot_key
from ens_evidence.evidence.layout import is_evidence_id
from ens_evidence.evidence.manifests import ManifestError, load_manifest_by_id
from ens_evidence.evidence.verify import verify_one
from ens_evidence.models.preaudit import (
    BLOCKING_PROPOSALS,
    NEXT_STATES,
    SEVERITY,
    UNBLOCKING,
    Disposition,
    DispositionRecord,
    DispositionState,
    Finding,
    Layer,
    LayerStatus,
    PreAuditRecord,
    RefKind,
)
from ens_evidence.models.readiness import OpenFinding
from ens_evidence.preaudit.records import (
    DISPOSITIONS_PREFIX,
    RUNS_PREFIX,
    scan_dispositions,
    scan_runs,
)
from ens_evidence.workspace import NotFound, WorkspaceStore

FINDING_DETAIL = "preaudit_finding"
BROKEN_DETAIL = "preaudit_history_broken"

FROZEN_FIELDS = ("preaudit_runs", "preaudit_dispositions")
"""Los campos de `SnapshotInputs` con las claves de pre-auditoría congeladas al generar."""


@dataclass(frozen=True)
class FindingState:
    """Un hallazgo de juicio, las ejecuciones que lo proponen (en orden de secuencia) y la
    disposición que cubre la última (None: abierto).

    `finding` es la aparición que manda: la más grave de las que no cubre ninguna disposición que
    desbloquea (a igual gravedad, la más reciente). Una NC nunca deja de bloquear porque antes se
    propusiera como observación."""

    finding: Finding
    runs: tuple[PreAuditRecord, ...]
    disposition: Disposition | None = None

    @property
    def run_seq(self) -> int:
        """La primera ejecución que lo propuso."""
        return self.runs[0].seq

    @property
    def last_run(self) -> PreAuditRecord:
        return self.runs[-1]

    @property
    def last_run_seq(self) -> int:
        return self.last_run.seq

    @property
    def dossier(self) -> str:
        """El expediente de la última ejecución que lo propuso."""
        return self.last_run.run.dossier

    @property
    def dossiers(self) -> frozenset[str]:
        """Los expedientes de todas las ejecuciones que lo propusieron."""
        return frozenset(r.run.dossier for r in self.runs)

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
    expediente (None: todas las que hay). En los dos casos, lo que congeló cualquier expediente
    generado antes de `at` tiene que seguir ahí."""
    run_scan = scan_runs(store, now=at, known=runs)
    disposition_scan = scan_dispositions(store, now=at, known=dispositions)
    problems = [*run_scan.problems, *disposition_scan.problems, *frozen_problems(store, at)]
    proposed = _appearances(run_scan.records)
    decided, decision_problems = _decisions(store, disposition_scan.records, proposed)
    states = tuple(_state(seen, decided.get(fid, [])) for fid, seen in proposed.items())
    executed, sha_problems = _judgment_executed(store, at, run_scan.records)
    problems += decision_problems + sha_problems
    return FindingsView(states, executed, tuple(problems))


type Appearance = tuple[chain.Stored[PreAuditRecord], Finding]


def _appearances(
    records: Iterable[chain.Stored[PreAuditRecord]],
) -> dict[str, list[Appearance]]:
    """Cada hallazgo de juicio con sus apariciones (ejecución y propuesta), en orden de
    secuencia."""
    proposed: dict[str, list[Appearance]] = {}
    for stored in records:
        for finding in stored.record.findings:
            if finding.layer is Layer.JUDGMENT:
                proposed.setdefault(finding.id, []).append((stored, finding))
    return proposed


def _state(seen: list[Appearance], history: list[Disposition]) -> FindingState:
    last = seen[-1][0].record.seq
    covering = history[-1] if history and history[-1].covers_run == last else None
    cleared = max((d.covers_run for d in history if d.state in UNBLOCKING), default=0)
    pending = [finding for stored, finding in seen if stored.record.seq > cleared]
    ruling = min(reversed(pending or [seen[-1][1]]), key=lambda f: SEVERITY.index(f.proposal))
    return FindingState(ruling, tuple(stored.record for stored, _ in seen), covering)


def _decisions(
    store: WorkspaceStore,
    records: Iterable[chain.Stored[DispositionRecord]],
    proposed: dict[str, list[Appearance]],
) -> tuple[dict[str, list[Disposition]], list[str]]:
    """Las disposiciones válidas de cada hallazgo, recorriendo la cadena en orden. Una
    disposición que cubre una ejecución posterior a la anterior parte de `OPEN`. Una `RESOLVED`
    válida cuya evidencia ya no prueba la corrección cuenta, pero deja un problema."""
    decided: dict[str, list[Disposition]] = {}
    problems: list[str] = []
    for stored in records:
        disposition = stored.record.disposition
        if disposition.finding_id not in proposed:
            problems.append(f"{stored.key}: dispone un hallazgo que ninguna ejecución propuso")
            continue
        seen = [run for run, _ in proposed[disposition.finding_id]]
        order = _order_problems(stored, seen)
        history = decided.setdefault(disposition.finding_id, [])
        current = history[-1] if history else None
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
        history.append(disposition)
        if disposition.state is DispositionState.RESOLVED:
            problems += _resolution_problems(store, stored, proposed[disposition.finding_id])
    return decided, problems


def _resolution_problems(
    store: WorkspaceStore, stored: chain.Stored[DispositionRecord], seen: list[Appearance]
) -> list[str]:
    """Lo que hoy impide que la evidencia de una `RESOLVED` pruebe la corrección."""
    disposition = stored.record.disposition
    evidence_id = disposition.evidence_id or ""
    covered, finding = next((s, f) for s, f in seen if s.record.seq == disposition.covers_run)
    prefix = (
        f"{stored.key}: el hallazgo {disposition.finding_id} se resolvió con la evidencia "
        f"{evidence_id or '(ninguna)'}, que"
    )
    integrity = verify_one(store, evidence_id).problems
    if integrity:
        return [f"{prefix} ya no se verifica ({problem})" for problem in integrity]
    why = correction_problems(
        store, evidence_id, finding, covered.record, stored.record.recorded_at
    )
    return [f"{prefix} {reason}" for reason in why]


def correction_problems(
    store: WorkspaceStore,
    evidence_id: str,
    finding: Finding,
    covered: PreAuditRecord,
    until: datetime,
) -> list[str]:
    """Por qué una evidencia íntegra no prueba que se corrigió `finding` (vacío: lo prueba).

    `RESOLVED` no se apoya en lo que prueba el incumplimiento: la evidencia no puede ser una de
    las que cita el hallazgo, ni haberse recogido antes de la ejecución `covered` que cubre, ni
    después de `until` (el instante de la disposición). La integridad la comprueba `verify_one`;
    aquí, un id o un manifiesto ilegible no añade nada."""
    if evidence_id in {ref.id for ref in finding.refs if ref.kind is RefKind.EVIDENCE}:
        return ["es la que cita el hallazgo como prueba"]
    if not is_evidence_id(evidence_id):
        return []
    try:
        collected = load_manifest_by_id(store, evidence_id).collected_at
    except ManifestError:
        return []
    if collected <= covered.recorded_at:
        return [f"se recogió antes de la ejecución {covered.seq} que propuso el hallazgo"]
    if collected > until:
        return ["tiene fecha de recogida en el futuro"]
    return []


def _order_problems(
    stored: chain.Stored[DispositionRecord], seen: list[chain.Stored[PreAuditRecord]]
) -> list[str]:
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


def current_dossier(store: WorkspaceStore, at: datetime) -> str | None:
    """El último expediente generado antes de `at` (por el instante de su id)."""
    return max(dossiers_before(store, at), default=None)


def current_dossier_run(store: WorkspaceStore, at: datetime) -> PreAuditRecord | None:
    """La última ejecución legible registrada en o antes de `at` sobre el expediente vigente.
    Informa; no decide nada (eso es de `findings_view`)."""
    dossier = current_dossier(store, at)
    runs = [
        s.record
        for s in scan_runs(store, now=at).records
        if s.record.run.dossier == dossier and s.record.recorded_at <= at
    ]
    return max(runs, key=lambda r: r.seq, default=None)


def frozen_problems(store: WorkspaceStore, at: datetime) -> list[str]:
    """Lo que congeló un expediente generado antes de `at` y ya no está: historia rota.

    Cada expediente guarda las claves de pre-auditoría que existían al generarlo
    (`SnapshotInputs`), y es una copia inmutable con su huella en el id. Borrar o rehacer una
    ejecución o una disposición congelada se ve aquí; para borrarla sin que se vea habría que
    alterar también cada expediente que la congeló."""
    problems: list[str] = []
    for dossier in dossiers_before(store, at):
        keys, unreadable = _frozen_by(store, dossier)
        problems += unreadable + missing_frozen(store, dossier, keys)
    return problems


def missing_frozen(store: WorkspaceStore, dossier: str, keys: Iterable[str]) -> list[str]:
    """Las claves de pre-auditoría que congeló `dossier` y ya no están. Que una siga ahí con
    otro contenido lo detecta la lectura de la cadena: cada clave lleva la huella de su registro."""
    present = {*store.list(RUNS_PREFIX), *store.list(DISPOSITIONS_PREFIX)}
    return [
        f"{key}: lo congeló el expediente {dossier} y ya no está"
        for key in keys
        if key not in present
    ]


def _frozen_by(store: WorkspaceStore, dossier: str) -> tuple[list[str], list[str]]:
    """(claves de pre-auditoría que congeló `dossier`, problema si no se pueden leer)."""
    key = snapshot_key(dossier)
    unreadable = f"{key}: no se lee; no se comprueban las pre-auditorías que congeló"
    try:
        data = store.read_bytes(key)
    except NotFound:
        return [], [unreadable]
    if not matches_content(dossier, data):
        return [], [f"{key}: el contenido no coincide con la huella del id"]
    try:
        inputs = json.loads(data).get("inputs") or {}
        keys = [k for name in FROZEN_FIELDS for k in inputs.get(name) or []]
    except (ValueError, AttributeError, TypeError):
        return [], [unreadable]
    if not all(isinstance(k, str) for k in keys):
        return [], [unreadable]
    return keys, []


def _judgment_executed(
    store: WorkspaceStore, at: datetime, records: Iterable[chain.Stored[PreAuditRecord]]
) -> tuple[bool, list[str]]:
    dossier = current_dossier(store, at)
    done = [
        s
        for s in records
        if s.record.run.dossier == dossier and s.record.run.judgment_status is LayerStatus.DONE
    ]
    if dossier is None or not done:
        return False, []
    try:
        data = store.read_bytes(snapshot_key(dossier))
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
            measure_id=s.finding.measure,
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
