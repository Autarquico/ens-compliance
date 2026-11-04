"""`PreAuditFindings`: qué bloquea (`OPEN_FINDING`) y cuándo se ha ejecutado el juicio, sobre
cadenas escritas a mano. Una ejecución nueva nunca cierra nada; una cadena rota bloquea."""

import hashlib
from datetime import UTC, datetime, timedelta

import pytest

from ens_evidence import chain
from ens_evidence.models.preaudit import (
    Classification,
    Consent,
    DeterministicRule,
    Disposition,
    DispositionRecord,
    DispositionState,
    Finding,
    Layer,
    LayerStatus,
    PreAuditRecord,
    PreAuditRun,
    Provider,
    Ref,
    RefKind,
    finding_id,
)
from ens_evidence.preaudit.findings import (
    BROKEN_DETAIL,
    FINDING_DETAIL,
    PreAuditFindings,
    current_dossier,
    findings_view,
)
from ens_evidence.preaudit.records import DISPOSITIONS_PREFIX, RUNS_PREFIX
from ens_evidence.workspace import LocalStore

T0 = datetime(2026, 10, 1, 12, tzinfo=UTC)
DOSSIER_BYTES = b'{"dossier": 1}\n'


@pytest.fixture
def store(tmp_path):
    return LocalStore(tmp_path / "ws")


def dossier_at(store, at: datetime, data: bytes = DOSSIER_BYTES) -> str:
    dossier = f"{chain.instant_stamp(at)}-{hashlib.sha256(data).hexdigest()[:12]}"
    store.create_bytes(f"evidence/assessments/{dossier}/dossier.json", data)
    return dossier


def judged(proposal=Classification.NC_MENOR, measure="op.mon.1") -> Finding:
    refs = [Ref(kind=RefKind.DOCUMENT, id="politica-seguridad")]
    criterion = "coherencia-politica-evidencia"
    return Finding(
        id=finding_id(criterion, measure, refs),
        layer=Layer.JUDGMENT,
        proposal=proposal,
        measure=measure,
        refs=refs,
        criterion=criterion,
        reason="La política exige lo que la evidencia no muestra.",
        quote="texto citado",
        source="ITS de Auditoría §VI.2",
    )


def deterministic() -> Finding:
    criterion = DeterministicRule.CHECK_FAIL.value
    return Finding(
        id=finding_id(criterion, "op.acc.5", []),
        layer=Layer.DETERMINISTIC,
        proposal=Classification.NC_MENOR,
        measure="op.acc.5",
        criterion=criterion,
        reason="check FAIL",
        source="IC-01/19 §53",
    )


def append(store, prefix: str, record) -> str:
    data = chain.render_record(record.model_dump(mode="json"))
    chain.reserve_sequence(store, prefix, record.seq)
    key = chain.record_key(prefix, record.seq, record.recorded_at, data)
    store.create_bytes(key, data)
    return key


def _last(store, prefix: str) -> tuple[int, str | None]:
    keys = sorted(k for k in store.list(prefix) if "/seq/" not in k)
    if not keys:
        return 0, None
    return len(keys), hashlib.sha256(store.read_bytes(keys[-1])).hexdigest()


def add_run(store, dossier: str, at: datetime, findings=(), *, judged_status=LayerStatus.DONE):
    seq, previous = _last(store, RUNS_PREFIX)
    sha = hashlib.sha256(store.read_bytes(f"evidence/assessments/{dossier}/dossier.json"))
    judgment = judged_status is not LayerStatus.NOT_RUN
    consent = Consent(by="Ana", at=at, payload_sha256="0" * 64, destination="anthropic x")
    run = PreAuditRun(
        dossier=dossier,
        dossier_sha256=sha.hexdigest(),
        pack_version="1.0.0",
        pack_sha256="1" * 64,
        at=at,
        provider=Provider.ANTHROPIC if judgment else None,
        model="m" if judgment else None,
        consent=consent if judgment else None,
        judgment_status=judged_status,
        judgment_error="salida inválida" if judged_status is LayerStatus.ERROR else None,
    )
    record = PreAuditRecord(
        seq=seq + 1,
        previous_sha256=previous,
        recorded_at=at,
        prepared="evidence/preaudits/p/prepared.json" if judgment else None,
        run=run,
        findings=list(findings),
    )
    return append(store, RUNS_PREFIX, record)


def dispose(
    store, finding: Finding, state: DispositionState, at: datetime, *, covers: int | None = None
) -> str:
    """Por defecto cubre la última ejecución que propuso el hallazgo, como `preaudit review`."""
    seq, previous = _last(store, DISPOSITIONS_PREFIX)
    if covers is None:
        covers = findings_view(store, at).state_of(finding.id).last_run_seq
    disposition = Disposition(
        finding_id=finding.id,
        covers_run=covers,
        state=state,
        by="Ana",
        at=at,
        evidence_id="EV-20261001-abcdef12" if state is DispositionState.RESOLVED else None,
        reason="motivo" if state is DispositionState.DISMISSED else None,
    )
    record = DispositionRecord(
        seq=seq + 1, previous_sha256=previous, recorded_at=at, disposition=disposition
    )
    return append(store, DISPOSITIONS_PREFIX, record)


def blocking(store, at, source=None) -> tuple[list[str], bool]:
    found, executed = (source or PreAuditFindings()).open_findings(store, at)
    return [f.id for f in found], executed


# --- qué bloquea -------------------------------------------------------------------------------


def test_without_runs_nothing_blocks_and_judgment_has_not_run(store):
    assert blocking(store, T0) == ([], False)


@pytest.mark.parametrize("proposal", [Classification.NC_MAYOR, Classification.NC_MENOR])
def test_an_undisposed_nonconformity_blocks(store, proposal):
    dossier = dossier_at(store, T0)
    finding = judged(proposal)
    add_run(store, dossier, T0 + timedelta(minutes=1), [finding])

    found, executed = blocking(store, T0 + timedelta(minutes=2))

    assert found == [f"{FINDING_DETAIL}:{finding.id}"] and executed


def test_an_observation_and_a_deterministic_finding_do_not_block(store):
    dossier = dossier_at(store, T0)
    add_run(store, dossier, T0 + timedelta(minutes=1), [judged(Classification.OBSERVACION)])
    add_run(
        store,
        dossier,
        T0 + timedelta(minutes=2),
        [deterministic()],
        judged_status=LayerStatus.NOT_RUN,
    )

    assert blocking(store, T0 + timedelta(minutes=3)) == ([], True)


def test_accepted_still_blocks_and_resolved_or_dismissed_unblock(store):
    dossier = dossier_at(store, T0)
    mayor, menor = judged(Classification.NC_MAYOR, "op.pl.1"), judged(measure="op.pl.2")
    add_run(store, dossier, T0 + timedelta(minutes=1), [mayor, menor])
    dispose(store, mayor, DispositionState.ACCEPTED, T0 + timedelta(minutes=2))

    assert len(blocking(store, T0 + timedelta(minutes=3))[0]) == 2

    dispose(store, mayor, DispositionState.RESOLVED, T0 + timedelta(minutes=4))
    dispose(store, menor, DispositionState.DISMISSED, T0 + timedelta(minutes=5))

    assert blocking(store, T0 + timedelta(minutes=6)) == ([], True)


def test_a_new_run_never_closes_an_earlier_finding(store):
    dossier = dossier_at(store, T0)
    finding = judged()
    add_run(store, dossier, T0 + timedelta(minutes=1), [finding])
    add_run(store, dossier, T0 + timedelta(minutes=2), [])
    add_run(store, dossier, T0 + timedelta(minutes=3), judged_status=LayerStatus.ERROR)
    add_run(store, dossier, T0 + timedelta(minutes=4), judged_status=LayerStatus.NOT_RUN)

    assert blocking(store, T0 + timedelta(minutes=5))[0] == [f"{FINDING_DETAIL}:{finding.id}"]


def test_a_finding_raised_again_is_one_finding(store):
    dossier = dossier_at(store, T0)
    add_run(store, dossier, T0 + timedelta(minutes=1), [judged()])
    add_run(store, dossier, T0 + timedelta(minutes=2), [judged()])

    view = findings_view(store, T0 + timedelta(minutes=3))

    assert [s.run_seq for s in view.states] == [1]


# --- reaparición -------------------------------------------------------------------------------


def minutes(n: int) -> datetime:
    return T0 + timedelta(minutes=n)


@pytest.mark.parametrize("decision", [DispositionState.RESOLVED, DispositionState.DISMISSED])
def test_a_later_run_raising_a_disposed_finding_again_reopens_it(store, decision):
    dossier = dossier_at(store, T0)
    finding = judged()
    add_run(store, dossier, minutes(1), [finding])
    dispose(store, finding, decision, minutes(2))
    add_run(store, dossier, minutes(3), [finding])

    view = findings_view(store, minutes(4))

    state = view.state_of(finding.id)
    assert state.state is DispositionState.OPEN and state.blocks and not view.problems
    assert blocking(store, minutes(4))[0] == [f"{FINDING_DETAIL}:{finding.id}"]


def test_a_later_run_that_does_not_raise_it_keeps_it_resolved(store):
    dossier = dossier_at(store, T0)
    finding = judged()
    add_run(store, dossier, minutes(1), [finding])
    dispose(store, finding, DispositionState.RESOLVED, minutes(2))
    add_run(store, dossier, minutes(3), [judged(measure="op.pl.1")])

    assert findings_view(store, minutes(4)).state_of(finding.id).state is DispositionState.RESOLVED


def test_a_disposition_after_the_reappearance_unblocks(store):
    dossier = dossier_at(store, T0)
    finding = judged()
    add_run(store, dossier, minutes(1), [finding])
    dispose(store, finding, DispositionState.RESOLVED, minutes(2))
    add_run(store, dossier, minutes(3), [finding])
    dispose(store, finding, DispositionState.DISMISSED, minutes(4))

    state = findings_view(store, minutes(5)).state_of(finding.id)
    assert state.state is DispositionState.DISMISSED and state.disposition.covers_run == 2
    assert blocking(store, minutes(5)) == ([], True)


def test_resolved_stays_terminal_for_the_runs_it_covers(store):
    dossier = dossier_at(store, T0)
    finding = judged()
    add_run(store, dossier, minutes(1), [finding])
    dispose(store, finding, DispositionState.RESOLVED, minutes(2))
    dispose(store, finding, DispositionState.ACCEPTED, minutes(3))

    assert broken(blocking(store, minutes(4))[0])


def test_a_disposition_that_covers_a_run_recorded_after_it_blocks(store):
    dossier = dossier_at(store, T0)
    finding = judged()
    add_run(store, dossier, minutes(1), [finding])
    add_run(store, dossier, minutes(3), [finding])
    dispose(store, finding, DispositionState.RESOLVED, minutes(2), covers=2)

    found, _ = blocking(store, minutes(4))
    assert broken(found)


def test_a_run_earlier_on_the_clock_but_not_covered_blocks(store):
    dossier = dossier_at(store, T0)
    finding = judged()
    add_run(store, dossier, minutes(1), [finding])
    add_run(store, dossier, minutes(2), [finding])
    dispose(store, finding, DispositionState.RESOLVED, minutes(3), covers=1)

    view = findings_view(store, minutes(4))
    assert any("anterior en el reloj" in p for p in view.problems)
    assert broken(blocking(store, minutes(4))[0])


def test_a_disposition_covering_a_run_that_did_not_raise_it_blocks(store):
    dossier = dossier_at(store, T0)
    finding = judged()
    add_run(store, dossier, minutes(1), [finding])
    add_run(store, dossier, minutes(2), [])
    dispose(store, finding, DispositionState.RESOLVED, minutes(3), covers=2)

    assert broken(blocking(store, minutes(4))[0])


# --- juicio ejecutado --------------------------------------------------------------------------


def test_judgment_counts_only_on_the_current_dossier(store):
    first = dossier_at(store, T0)
    add_run(store, first, T0 + timedelta(minutes=1))
    assert blocking(store, T0 + timedelta(minutes=2)) == ([], True)

    later = T0 + timedelta(minutes=3)
    second = dossier_at(store, later, b'{"dossier": 2}\n')

    assert current_dossier(store, later + timedelta(minutes=1)) == second
    assert blocking(store, later + timedelta(minutes=1)) == ([], False)


@pytest.mark.parametrize("status", [LayerStatus.ERROR, LayerStatus.NOT_RUN])
def test_a_judgment_that_did_not_finish_keeps_human_review_pending(store, status):
    dossier = dossier_at(store, T0)
    add_run(store, dossier, T0 + timedelta(minutes=1), judged_status=status)

    assert blocking(store, T0 + timedelta(minutes=2)) == ([], False)


# --- vigencia ----------------------------------------------------------------------------------


def broken(found: list[str]) -> bool:
    return any(f.startswith(f"{BROKEN_DETAIL}:") for f in found)


def test_an_altered_run_blocks_with_a_visible_error(store):
    dossier = dossier_at(store, T0)
    key = add_run(store, dossier, T0 + timedelta(minutes=1), [judged()])
    store.write_bytes(key, store.read_bytes(key).replace(b"NC_MENOR", b"OBSERVACION"))

    found, executed = blocking(store, T0 + timedelta(minutes=2))

    assert broken(found) and not executed


def test_an_unreadable_disposition_blocks(store):
    dossier = dossier_at(store, T0)
    finding = judged()
    add_run(store, dossier, T0 + timedelta(minutes=1), [finding])
    key = dispose(store, finding, DispositionState.DISMISSED, T0 + timedelta(minutes=2))
    store.write_bytes(key, b"no: [yaml")

    found, _ = blocking(store, T0 + timedelta(minutes=3))

    assert broken(found) and f"{FINDING_DETAIL}:{finding.id}" in found


def test_a_record_in_the_future_blocks(store):
    dossier = dossier_at(store, T0)
    finding = judged()
    add_run(store, dossier, T0 + timedelta(minutes=1), [finding])
    dispose(store, finding, DispositionState.DISMISSED, T0 + timedelta(minutes=5))

    found, _ = blocking(store, T0 + timedelta(minutes=3))

    assert broken(found)


def test_a_dossier_altered_after_its_run_blocks(store):
    dossier = dossier_at(store, T0)
    add_run(store, dossier, T0 + timedelta(minutes=1))
    store.write_bytes(f"evidence/assessments/{dossier}/dossier.json", b"{}\n")

    found, executed = blocking(store, T0 + timedelta(minutes=2))

    assert broken(found) and not executed


def test_an_invalid_disposition_sequence_blocks(store):
    dossier = dossier_at(store, T0)
    finding = judged()
    add_run(store, dossier, T0 + timedelta(minutes=1), [finding])
    dispose(store, finding, DispositionState.DISMISSED, T0 + timedelta(minutes=2))
    dispose(store, finding, DispositionState.ACCEPTED, T0 + timedelta(minutes=3))

    assert broken(blocking(store, T0 + timedelta(minutes=4))[0])


# --- congelado ---------------------------------------------------------------------------------


def test_frozen_keys_see_only_what_existed(store):
    dossier = dossier_at(store, T0)
    finding = judged()
    add_run(store, dossier, T0 + timedelta(minutes=1), [finding])
    frozen = PreAuditFindings(store.list(RUNS_PREFIX), store.list(DISPOSITIONS_PREFIX))
    dispose(store, finding, DispositionState.DISMISSED, T0 + timedelta(minutes=2))
    add_run(store, dossier, T0 + timedelta(minutes=3), [judged(measure="op.pl.1")])

    at = T0 + timedelta(minutes=4)
    assert blocking(store, at, frozen) == ([f"{FINDING_DETAIL}:{finding.id}"], True)
    assert len(blocking(store, at)[0]) == 1
    assert blocking(store, at)[0] != blocking(store, at, frozen)[0]


def test_a_frozen_record_that_disappeared_blocks(store):
    dossier = dossier_at(store, T0)
    key = add_run(store, dossier, T0 + timedelta(minutes=1), [judged()])
    frozen = PreAuditFindings(store.list(RUNS_PREFIX), [])
    (store._root / key).unlink()

    assert broken(blocking(store, T0 + timedelta(minutes=2), frozen)[0])
