"""Disposición humana de los hallazgos de juicio y su efecto en cada evaluación (`assess`).

Cada llamador de `assess` ve el hallazgo abierto como bloqueo, y deja de verlo solo tras
`RESOLVED` o `DISMISSED` (`assessment status` y la vigencia de punta a punta están en
`test_acceptance`; las reglas, en `test_findings`). El expediente congela las claves de la
pre-auditoría: lo que se dispone después no cambia su reproducción."""

import json
from datetime import timedelta

import pytest
from assessment.dossier.stages import ID, basic, run_at
from matrix.acceptance_helpers import import_manual_evidence
from reports.world import MADRID

from ens_evidence.assessment.dossier import render_snapshot_md
from ens_evidence.assessment.dossier.model import snapshot_export
from ens_evidence.assessment.dossier.store import load_snapshot
from ens_evidence.assessment.dossier.transitions import evaluate_for_declaration
from ens_evidence.assessment.status import assessment_status
from ens_evidence.checks.source import StoredCheckResults
from ens_evidence.evidence.layout import manifest_key
from ens_evidence.evidence.manifests import load_manifest
from ens_evidence.models.preaudit import DispositionState, Layer, RefKind
from ens_evidence.preaudit.findings import BROKEN_DETAIL, FINDING_DETAIL
from ens_evidence.preaudit.records import (
    DISPOSITIONS_PREFIX,
    RUNS_PREFIX,
    scan_dispositions,
    scan_runs,
)
from ens_evidence.preaudit.review import ReviewRejected, review
from ens_evidence.preaudit.service import prepare, run
from ens_evidence.reports.daily import build_daily
from ens_evidence.reports.monthly import build_monthly

from .helpers import CONTRADICTION, FakeAnthropic, traceable, with_evidence, world_at
from .test_service import ANTHROPIC, PREPARED_AT, SENT_AT

CORRECTED_AT = SENT_AT + timedelta(seconds=30)
REVIEWED_AT = SENT_AT + timedelta(minutes=1)
LATER = SENT_AT + timedelta(minutes=5)
DISPOSITIONS = {
    DispositionState.RESOLVED: {"evidence": True},
    DispositionState.DISMISSED: {"reason": "El documento citado ya no está en vigor."},
}


@pytest.fixture(scope="module")
def stages():
    return basic()


@pytest.fixture
def judged(stages, tmp_path, monkeypatch):
    """Un expediente revisado con la capa de juicio en DONE, un NC_MENOR abierto que cita una
    evidencia y otra, recogida después, que lo corrige: (mundo, expediente, corrección,
    hallazgo)."""
    world = world_at(stages, tmp_path, monkeypatch)
    dossier, evidence = with_evidence(world, tmp_path, f"{CONTRADICTION}\n", document=True)
    FakeAnthropic(lambda payload: traceable(payload, evidence)).install(monkeypatch)
    prepared = prepare(world.store, dossier, None, ANTHROPIC, now=PREPARED_AT)
    result = run(
        world.store, dossier, None, prepared.payload.sha256, "Ana", settings=ANTHROPIC, now=SENT_AT
    )
    [finding] = [f for f in result.record.findings if f.layer is Layer.JUDGMENT]
    correction = imported_at(world, tmp_path, CORRECTED_AT, "Doble factor activado.")
    return world, dossier, correction, finding


def imported_at(world, tmp_path, at, content: str) -> str:
    world.at(at)
    return import_manual_evidence(world.run, tmp_path, content=f"{content}\n")


def cited_evidence(finding) -> str:
    return next(ref.id for ref in finding.refs if ref.kind is RefKind.EVIDENCE)


def decide(store, finding, state, correction, *, at=REVIEWED_AT, by="Ana Pérez"):
    extra = DISPOSITIONS.get(state, {})
    return review(
        store,
        finding.id,
        state,
        by=by,
        now=at,
        evidence_id=correction if extra.get("evidence") else None,
        reason=extra.get("reason"),
    )


def finding_blocker(finding) -> str:
    return f"{FINDING_DETAIL}:{finding.id}"


# --- disposición -------------------------------------------------------------------------------


def test_resolved_needs_an_existing_and_intact_evidence(judged):
    world, _, correction, finding = judged

    with pytest.raises(ReviewRejected, match="--evidence: RESOLVED cita"):
        review(world.store, finding.id, DispositionState.RESOLVED, by="Ana", now=REVIEWED_AT)
    with pytest.raises(ReviewRejected, match="--evidence"):
        review(
            world.store,
            finding.id,
            DispositionState.RESOLVED,
            by="Ana",
            now=REVIEWED_AT,
            evidence_id="EV-20990101-00000000",
        )
    assert world.store.list(DISPOSITIONS_PREFIX) == []

    _, state = decide(world.store, finding, DispositionState.RESOLVED, correction)
    assert not state.blocks


def test_resolved_rejects_an_evidence_collected_before_the_run(judged, tmp_path):
    world, _, _, finding = judged
    earlier = imported_at(world, tmp_path, SENT_AT - timedelta(seconds=30), "Acta anterior.")

    with pytest.raises(ReviewRejected, match="se recogió antes de la ejecución 1"):
        decide(world.store, finding, DispositionState.RESOLVED, earlier)
    assert world.store.list(DISPOSITIONS_PREFIX) == []


def test_resolved_with_an_altered_evidence_is_rejected(judged):
    world, _, correction, finding = judged
    manifest = load_manifest(world.store, manifest_key(correction))
    world.store.write_bytes(manifest.artifact.key, b"alterado\n")

    with pytest.raises(ReviewRejected, match="--evidence"):
        decide(world.store, finding, DispositionState.RESOLVED, correction)


def test_dismissed_needs_a_reason(judged):
    world, _, _, finding = judged

    with pytest.raises(ReviewRejected, match="--reason: DISMISSED"):
        review(world.store, finding.id, DispositionState.DISMISSED, by="Ana", now=REVIEWED_AT)


@pytest.mark.parametrize(
    ("by", "reason", "message"),
    [
        ("ana@example.com", "motivo", "--by"),
        ("Ana", "Lo confirmó ana@example.com", "--reason"),
        ("Ana", "Clave ghp_" + "a" * 36, "--reason"),
    ],
)
def test_names_and_reasons_pass_the_privacy_detectors(judged, by, reason, message):
    world, _, _, finding = judged

    with pytest.raises(ReviewRejected, match=message):
        review(
            world.store,
            finding.id,
            DispositionState.DISMISSED,
            by=by,
            now=REVIEWED_AT,
            reason=reason,
        )
    assert world.store.list(DISPOSITIONS_PREFIX) == []


def test_an_unknown_finding_or_a_closed_one_is_rejected(judged):
    world, _, correction, finding = judged
    with pytest.raises(ReviewRejected, match="ninguna ejecución"):
        review(world.store, "0" * 64, DispositionState.ACCEPTED, by="Ana", now=REVIEWED_AT)

    decide(world.store, finding, DispositionState.DISMISSED, correction)

    with pytest.raises(ReviewRejected, match="DISMISSED; no admite ACCEPTED"):
        decide(world.store, finding, DispositionState.ACCEPTED, correction, at=LATER)


def test_dispositions_are_an_append_only_chain(judged):
    world, _, correction, finding = judged
    decide(world.store, finding, DispositionState.ACCEPTED, correction)
    decide(world.store, finding, DispositionState.RESOLVED, correction, at=LATER)

    scan = scan_dispositions(world.store, now=LATER)

    assert scan.problems == ()
    assert [s.record.disposition.state for s in scan.records] == [
        DispositionState.ACCEPTED,
        DispositionState.RESOLVED,
    ]
    assert scan.records[1].record.previous_sha256 == scan.records[0].sha256


# --- cada llamador de `assess` -----------------------------------------------------------------


def checks(at):
    return StoredCheckResults(reference_now=at)


def status_details(store, at) -> list[str]:
    return [b.detail for b in assessment_status(store, at, checks(at)).result.readiness.blockers]


def daily_lines(store, at) -> list[str]:
    report = build_daily(
        store,
        generated_at=at,
        period=at.date().isoformat(),
        tz=MADRID,
        cycle=None,
        checks=checks(at),
    )
    return report.readiness.value.blockers


def monthly_lines(store, at) -> list[str]:
    report = build_monthly(
        store, generated_at=at, period=at.strftime("%Y-%m"), tz=MADRID, checks=checks(at)
    )
    return [item.detail for item in report.pending.value.items]


def generated_details(world, at) -> list[str]:
    result = run_at(world, "assessment", "dossier", "generate", at=at)
    assert result.exit_code == 0, result.output
    snapshot = load_snapshot(world.store, ID.search(result.output).group())
    return [b.detail for b in snapshot_export(snapshot).blockers]


def declaration_problems(store, at) -> list[str]:
    return evaluate_for_declaration(store, at, checks(at))[1]


def blocks_in_daily(world, finding, at):
    return any(finding.id in line for line in daily_lines(world.store, at))


def blocks_in_monthly(world, finding, at):
    return any(finding.id in line for line in monthly_lines(world.store, at))


def blocks_in_generated_dossier(world, finding, at):
    return finding_blocker(finding) in generated_details(world, at)


def blocks_before_declare(world, finding, at):
    return any("hallazgo abierto" in p for p in declaration_problems(world.store, at))


CALLERS = [
    blocks_in_daily,
    blocks_in_monthly,
    blocks_in_generated_dossier,
    blocks_before_declare,
]


@pytest.mark.parametrize("blocks", CALLERS)
def test_an_open_nonconformity_blocks_every_caller(judged, blocks):
    world, _, correction, finding = judged
    decide(world.store, finding, DispositionState.ACCEPTED, correction)

    assert blocks(world, finding, LATER)


@pytest.mark.parametrize("blocks", CALLERS)
@pytest.mark.parametrize("state", list(DISPOSITIONS))
def test_resolved_or_dismissed_unblocks_every_caller(judged, blocks, state):
    world, _, correction, finding = judged
    decide(world.store, finding, state, correction)

    assert not blocks(world, finding, LATER)


def test_judgment_done_on_the_current_dossier_clears_pending_human_review(judged):
    world, _, _, _ = judged

    assert not assessment_status(world.store, LATER, checks(LATER)).pending_human_review


# --- congelado en el expediente ----------------------------------------------------------------


def verify(world, dossier, at):
    return run_at(world, "assessment", "dossier", "verify", dossier, at=at)


def test_a_dossier_still_verifies_after_its_preaudit_and_dispositions(judged):
    world, dossier, correction, finding = judged
    decide(world.store, finding, DispositionState.RESOLVED, correction)

    result = verify(world, dossier, LATER)

    assert result.exit_code == 0, result.output


def test_a_later_dossier_freezes_the_open_finding_and_still_verifies(judged):
    world, _, correction, finding = judged
    generated = run_at(world, "assessment", "dossier", "generate", at=REVIEWED_AT)
    assert generated.exit_code == 0, generated.output
    later = ID.search(generated.output).group()
    snapshot = load_snapshot(world.store, later)
    assert snapshot.inputs.preaudit_runs and snapshot.inputs.preaudit_dispositions == []
    assert finding_blocker(finding) in [b.detail for b in snapshot_export(snapshot).blockers]

    decide(world.store, finding, DispositionState.DISMISSED, correction, at=LATER)
    result = verify(world, later, LATER + timedelta(minutes=1))

    assert result.exit_code == 0, result.output


def test_a_later_dossier_says_which_preaudit_it_froze_in_section_9(judged):
    world, dossier, _, _ = judged
    [stored] = scan_runs(world.store).records
    deterministic = sum(f.layer is Layer.DETERMINISTIC for f in stored.record.findings)
    at = stored.record.recorded_at.isoformat()
    generated = run_at(world, "assessment", "dossier", "generate", at=REVIEWED_AT)
    assert generated.exit_code == 0, generated.output
    snapshot = load_snapshot(world.store, ID.search(generated.output).group())

    treatment = snapshot.sections[8]

    assert treatment.content["pre_audit"] == (
        f"pre-auditoría 1 del {at} sobre el expediente {dossier}: capa determinista ejecutada, "
        f"{deterministic} hallazgo{'' if deterministic == 1 else 's'}; capa de juicio "
        "ejecutada, 1 hallazgo. Cada hallazgo es una propuesta; revisión humana pendiente"
    )
    assert stored.key in {source.key for source in treatment.sources}
    assert f"Pre-auditoría 1 del {at} sobre el expediente {dossier}" in render_snapshot_md(snapshot)


def delete(world, prefix: str) -> None:
    for key in world.store.list(prefix):
        (world.store._root / key).unlink()


@pytest.mark.parametrize("prefix", [RUNS_PREFIX, DISPOSITIONS_PREFIX])
def test_deleting_the_preaudit_history_a_dossier_froze_blocks_and_fails_verify(judged, prefix):
    world, _, correction, finding = judged
    decide(world.store, finding, DispositionState.DISMISSED, correction)
    generated = run_at(world, "assessment", "dossier", "generate", at=LATER)
    assert generated.exit_code == 0, generated.output
    later = ID.search(generated.output).group()
    at = LATER + timedelta(minutes=1)
    assert not any(d.startswith(BROKEN_DETAIL) for d in status_details(world.store, at))

    delete(world, prefix)

    assert any(d.startswith(BROKEN_DETAIL) for d in status_details(world.store, at))
    assert any("hallazgo abierto" in p for p in declaration_problems(world.store, at))
    result = verify(world, later, at)
    assert result.exit_code != 0
    assert f"lo congeló el expediente {later} y ya no está" in result.output


# --- CLI ---------------------------------------------------------------------------------------


def test_cli_review_and_show_report_the_state(judged):
    world, _, correction, finding = judged
    world.at(REVIEWED_AT)

    reviewed = world.run(
        "preaudit", "review", finding.id, "--by", "Ana Pérez", "--decision", "resolved",
        "--evidence", correction,
    )  # fmt: skip
    world.at(LATER)
    shown = world.run("preaudit", "show", "1")
    status = world.run("assessment", "status", "--format", "json")

    assert reviewed.exit_code == 0, reviewed.output
    assert "RESOLVED registrado" in reviewed.output and "no bloquea" in reviewed.output
    assert f"Id: {finding.id}" in shown.output and "Disposición: RESOLVED" in shown.output
    details = [b["detail"] for b in json.loads(status.output)["blockers"]]
    assert finding_blocker(finding) not in details


def test_cli_review_rejects_a_dismissal_without_reason(judged):
    world, _, _, finding = judged
    world.at(REVIEWED_AT)

    result = world.run("preaudit", "review", finding.id, "--by", "Ana", "--decision", "dismissed")

    assert result.exit_code == 1, result.output
    assert "--reason" in result.output
    assert world.store.list(DISPOSITIONS_PREFIX) == []


def test_dossier_status_shows_the_findings_as_a_derived_view(judged):
    world, dossier, _, finding = judged

    result = run_at(world, "assessment", "dossier", "status", dossier, at=LATER)

    assert "Pre-auditoría (vista derivada; la instantánea no cambia):" in result.output
    assert f"{finding.id} (ejecución 1): OPEN; bloquea la preparación" in result.output


def test_the_open_finding_names_the_next_step(judged):
    world, _, _, finding = judged

    lines = daily_lines(world.store, LATER)

    [line] = [line for line in lines if finding.id in line]
    assert "preaudit review ID --by NOMBRE" in line
    assert line.count(finding.id) == 1
