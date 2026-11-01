from datetime import timedelta

import pytest
from pydantic import ValidationError

from ens_evidence.incidents import IncidentRecord, check_successor

from .helpers import (
    DETECTED,
    EVIDENCE,
    RECORDED,
    SHA,
    closed_state,
    closure,
    procedure,
    record,
    state,
)


def rejects(match: str, **fields) -> None:
    with pytest.raises(ValidationError, match=match):
        record(**fields)


def closed(**fields) -> IncidentRecord:
    return record(seq=2, kind="closed", previous_sha256=SHA, state=closed_state(**fields))


def test_minimal_open_incident_is_valid_and_unknown_personal_data_is_default():
    created = record()
    assert created.state.status == "open"
    assert created.state.personal_data == "unknown"
    assert created.state.simulated is False


@pytest.mark.parametrize(
    "incident_id", ["INC-26-001", "inc-2026-001", "INC-2026-1", "XXX-2026-001", "INC-2026-0001"]
)
def test_id_format(incident_id):
    rejects("id", state=state(id=incident_id))


def test_simulated_must_match_id_prefix():
    rejects("prefijo", state=state(simulated=True))
    rejects("prefijo", state=state(id="SIM-2026-001", procedure=procedure()))


def test_simulation_requires_procedure():
    rejects("exige `procedure`", state=state(id="SIM-2026-001", simulated=True))
    assert record(state=state(id="SIM-2026-001", simulated=True, procedure=procedure()))


def test_real_incident_without_procedure_is_valid():
    assert record().state.procedure is None


def test_status_and_closure_go_together():
    rejects("closure", state=state(status="closed", personal_data="no"))
    rejects("closure", state=state(closure=closure()))


def test_record_kind_and_sequence():
    rejects("primer registro", kind="updated")
    rejects("primer registro", seq=2, previous_sha256=SHA)
    rejects("previous_sha256", seq=2, kind="updated")
    rejects("previous_sha256", previous_sha256=SHA)


def test_closed_kind_needs_closed_state():
    rejects("closed", seq=2, kind="closed", previous_sha256=SHA)
    rejects("closed", seq=2, kind="updated", previous_sha256=SHA, state=closed_state())


def test_detected_at_not_after_recorded_at():
    rejects("detected_at", recorded_at=DETECTED - timedelta(seconds=1))


def test_actions_not_after_recorded_at():
    action = {"kind": "other", "at": RECORDED + timedelta(seconds=1), "description": "Aviso"}
    rejects("actuación", state=state(actions=[action]))


def test_naive_datetimes_are_rejected():
    rejects("recorded_at", recorded_at="2026-10-15T14:00:00")


def test_closed_at_between_detected_and_recorded():
    rejects(
        "closed_at", seq=2, kind="closed", previous_sha256=SHA,
        state=closed_state(closure=closure(closed_at=DETECTED - timedelta(hours=1))),
    )  # fmt: skip
    rejects(
        "closed_at", seq=2, kind="closed", previous_sha256=SHA,
        state=closed_state(closure=closure(closed_at=RECORDED + timedelta(hours=1))),
    )  # fmt: skip
    assert closed(closure=closure(closed_at=DETECTED)).state.closure


def test_valid_close():
    assert closed().state.status == "closed"


def test_close_requires_known_personal_data():
    rejects(
        "personal_data", seq=2, kind="closed", previous_sha256=SHA,
        state=closed_state(personal_data="unknown"),
    )  # fmt: skip


@pytest.mark.parametrize(
    "conclusion", ["", "   ", "pendiente", "N/A", "TBD", "Lorem ipsum dolor", "por definir"]
)
def test_conclusion_is_mandatory_and_not_filler(conclusion):
    with pytest.raises(ValidationError, match="conclusion"):
        closed(closure=closure(conclusion=conclusion))


def test_legal_relevance_yes_needs_advice_and_preserved_evidence():
    with pytest.raises(ValidationError, match="asesoramiento"):
        closed(closure=closure(legal_relevance="yes", preserved_evidence_ids=[EVIDENCE]))
    with pytest.raises(ValidationError, match="evidencias preservadas"):
        closed(closure=closure(legal_relevance="yes", legal_advice="Despacho Externo SL"))
    ok = closed(
        closure=closure(
            legal_relevance="yes",
            legal_advice="Despacho Externo SL",
            preserved_evidence_ids=[EVIDENCE],
        )
    )
    assert ok.state.closure.legal_relevance == "yes"


@pytest.mark.parametrize("relevance", ["no", "unknown"])
def test_legal_relevance_not_yes_needs_no_advice(relevance):
    assert closed(closure=closure(legal_relevance=relevance))


def test_auditable_events_review_is_mandatory_with_concrete_detail():
    without = closure()
    del without["auditable_events_review"]
    with pytest.raises(ValidationError, match="auditable_events_review"):
        closed(closure=without)
    for detail in ("", "pendiente"):
        bad = closure(auditable_events_review={"result": "changed", "detail": detail})
        with pytest.raises(ValidationError, match="detail"):
            closed(closure=bad)
    with pytest.raises(ValidationError, match="result"):
        closed(closure=closure(auditable_events_review={"result": "maybe", "detail": "algo"}))
    changed = closure(
        auditable_events_review={"result": "changed", "detail": "Se audita el login."}
    )
    assert closed(closure=changed)


@pytest.mark.parametrize(
    ("where", "value"),
    [("title", "x" * 121), ("analysis", {"summary": "x" * 2001})],
)
def test_length_limits_open_state(where, value):
    rejects(where, state=state(**{where: value}))


def test_length_limits_at_boundary_are_accepted():
    ok = record(state=state(title="x" * 120, analysis={"summary": "x" * 2000}))
    assert len(ok.state.title) == 120


def test_action_and_conclusion_limits():
    long_action = {"kind": "other", "at": DETECTED, "description": "x" * 501}
    rejects("description", state=state(actions=[long_action]))
    with pytest.raises(ValidationError, match="conclusion"):
        closed(closure=closure(conclusion="x" * 2001))
    assert closed(closure=closure(conclusion="x" * 2000))


def test_action_kinds_are_the_closed_vocabulary():
    rejects("kind", state=state(actions=[{"kind": "magic", "at": DETECTED, "description": "x"}]))


@pytest.mark.parametrize("alert", ["has space", "a@b.es", "", "x" * 129])
def test_alert_ids_are_opaque(alert):
    rejects("alert_ids", state=state(source={"system": "wazuh", "alert_ids": [alert]}))


def test_evidence_refs_have_ev_format():
    rejects("evidence_ids", state=state(evidence_ids=["EV-1"]))
    assert record(state=state(evidence_ids=[EVIDENCE]))


def test_procedure_fields():
    rejects("document_sha256", state=state(procedure={**procedure(), "document_sha256": "zz"}))
    rejects("approval_key", state=state(procedure={**procedure(), "approval_key": ""}))
    missing = procedure()
    del missing["approved_at"]
    rejects("approved_at", state=state(procedure=missing))


def test_unknown_fields_are_forbidden():
    rejects("extra", state=state(attachment="x"))
    rejects("extra", raw_log="x")


class TestSuccessor:
    def test_valid_update_has_no_errors(self):
        nxt = record(
            seq=2, kind="updated", previous_sha256=SHA, recorded_at=RECORDED + timedelta(hours=1)
        )
        assert check_successor(record(), nxt) == []

    def test_simulated_is_immutable(self):
        first = record(state=state(id="SIM-2026-001", simulated=True, procedure=procedure()))
        second = record(seq=2, kind="updated", previous_sha256=SHA, state=state())
        assert "simulated es inmutable" in check_successor(first, second)

    def test_detected_at_id_and_procedure_are_immutable(self):
        first = record(state=state(procedure=procedure()))
        second = record(
            seq=2, kind="updated", previous_sha256=SHA,
            state=state(id="INC-2026-002", detected_at=DETECTED + timedelta(minutes=1)),
        )  # fmt: skip
        errors = check_successor(first, second)
        assert {"id es inmutable", "detected_at es inmutable", "procedure es inmutable"} <= set(
            errors
        )

    def test_sequence_time_and_closed_are_enforced(self):
        later = record(recorded_at=RECORDED + timedelta(hours=1))
        gap = record(seq=3, kind="updated", previous_sha256=SHA, recorded_at=RECORDED)
        assert {"la secuencia debe ser contigua", "recorded_at no puede decrecer"} <= set(
            check_successor(later, gap)
        )
        after_close = record(seq=3, kind="updated", previous_sha256=SHA, state=state())
        assert "un incidente cerrado no admite más registros" in check_successor(
            closed(), after_close
        )
