import pytest

from ens_evidence.evidence import load_manifest_by_id
from ens_evidence.evidence.layout import artifact_key, manifest_key
from ens_evidence.incidents import (
    IncidentRecord,
    verify_all_incidents,
    verify_incident,
)
from ens_evidence.incidents.store import record_key, render_record, seq_key
from ens_evidence.incidents.verify import scan_incident

from .helpers import RECORDED
from .world import World

ID = "INC-2026-001"


def problems(world: World, incident_id: str = ID) -> str:
    return "\n".join(verify_incident(world.store, incident_id))


def stored(world: World, seq: int):
    return next(s for s in scan_incident(world.store, ID).records if s.record.seq == seq)


def variant(world: World, base: int, **changes) -> IncidentRecord:
    """Copia del registro `base` con campos de primer nivel cambiados."""
    return IncidentRecord.model_validate(
        {**stored(world, base).record.model_dump(mode="json"), **changes}
    )


@pytest.fixture
def three(world):
    world.open()
    world.update(hours=1)
    world.update(hours=2, analysis={"summary": "Credenciales reutilizadas.", "recommendations": []})
    return world


def test_a_clean_incident_has_no_problems(three):
    assert verify_incident(three.store, ID) == []
    assert verify_all_incidents(three.store) == {}


def test_a_tampered_record_does_not_match_its_key(three):
    key = stored(three, 2).key
    three.store.write_text(key, three.store.read_text(key).replace("Acceso", "Acceso editado"))

    assert "la clave no coincide con el contenido" in problems(three)


def test_duplicated_yaml_key_is_unreadable(three):
    key = stored(three, 2).key
    three.store.write_text(key, three.store.read_text(key) + "seq: 2\n")

    assert "clave repetida" in problems(three)
    assert "falta el registro 2" in problems(three)


def test_a_missing_record_leaves_a_gap(three):
    three.remove(stored(three, 2).key)

    assert "falta el registro 2" in problems(three)


def test_a_fork_is_two_records_with_the_same_number(three):
    state = stored(three, 2).record.state.model_dump(mode="json")
    three.forge(variant(three, 2, state={**state, "title": "Otro título"}))

    assert "bifurcación" in problems(three)


def test_a_broken_chain_is_reported(three):
    old = stored(three, 3).key
    three.forge(variant(three, 3, previous_sha256="d" * 64), old_key=old)

    assert "previous_sha256 está rota" in problems(three)


def test_recorded_at_cannot_decrease(three):
    old = stored(three, 3).key
    earlier = stored(three, 2).record.recorded_at.isoformat().replace("+00:00", "Z")
    three.forge(variant(three, 3, recorded_at=earlier.replace("15:00", "14:30")), old_key=old)

    assert "recorded_at no puede decrecer" in problems(three)


def test_the_first_record_must_be_opened(three):
    three.remove(stored(three, 1).key)

    assert "el primer registro no es `opened`" in problems(three)


def test_nothing_may_follow_a_closure(world):
    world.closed_incident()
    closed = stored(world, 3)
    state = {**closed.record.state.model_dump(mode="json"), "status": "open", "closure": None}
    world.forge(
        variant(
            world,
            3,
            seq=4,
            kind="updated",
            previous_sha256=closed.sha256,
            recorded_at=RECORDED.replace(hour=20).isoformat(),
            state=state,
        )
    )

    assert "no admite más registros" in problems(world)


def test_a_record_from_another_incident_is_reported(three):
    state = {**stored(three, 3).record.state.model_dump(mode="json"), "id": "INC-2026-002"}
    forged = variant(three, 3, state=state)
    data = render_record(forged.model_dump(mode="json"))
    key = record_key(forged, data).replace("INC-2026-002", ID)
    three.remove(stored(three, 3).key)
    three.store.create_bytes(key, data)

    assert "el registro es de otro incidente" in problems(three)


def test_a_missing_manifest_is_reported(three):
    manifest = next(iter(scan_incident(three.store, ID).evidence.values()))
    three.remove(manifest_key(manifest.id))

    assert "falta el manifiesto incident-record" in problems(three)


def test_an_artifact_that_changed_is_reported(three):
    manifest = next(iter(scan_incident(three.store, ID).evidence.values()))
    three.store.write_bytes(manifest.artifact.key, b"otro contenido")

    assert "el artefacto ha cambiado" in problems(three)


def test_a_missing_artifact_is_reported(three):
    manifest = next(iter(scan_incident(three.store, ID).evidence.values()))
    three.remove(artifact_key(manifest.artifact.sha256))

    assert "falta el artefacto" in problems(three)


def test_deleting_the_last_record_leaves_an_orphan_manifest_and_reservation(three):
    three.remove(stored(three, 3).key)

    found = problems(three)
    assert "manifiesto incident-record sin registro" in found
    assert "reserva de la secuencia 3 sin registro" in found


def test_deleting_the_last_record_and_its_reservation_still_leaves_the_manifest(three):
    three.remove(stored(three, 3).key)
    three.remove(seq_key(ID, 3))

    assert "manifiesto incident-record sin registro" in problems(three)


def test_a_reservation_without_record_blocks(three):
    three.store.create_bytes(seq_key(ID, 4), b"reservada\n")

    assert "reserva de la secuencia 4 sin registro" in problems(three)


def test_an_unexpected_key_is_reported(three):
    three.store.write_text(f"incidents/{ID}/notas.txt", "x")

    assert "clave inesperada" in problems(three)


def test_a_broken_evidence_reference_is_reported(world):
    evidence_id = world.evidence()
    world.open(evidence_ids=[evidence_id])
    assert verify_incident(world.store, ID) == []
    manifest = load_manifest_by_id(world.store, evidence_id)
    world.store.write_bytes(manifest.artifact.key, b"alterado")

    assert f"referencia EV rota: {evidence_id}" in problems(world)


def test_a_replaced_procedure_approval_is_reported(world, monkeypatch):
    world.approve_procedure(monkeypatch)
    procedure = world.procedure().model_dump(mode="json")
    world.open(id="SIM-2026-001", simulated=True, procedure=procedure)
    assert verify_incident(world.store, "SIM-2026-001") == []

    world.write_approval("d" * 64)

    assert "no está en el historial de org-docs" in problems(world, "SIM-2026-001")


def test_verify_all_reports_incidents_whose_files_were_all_deleted(world):
    world.open()
    for key in world.keys(f"incidents/{ID}"):
        world.remove(key)

    found = verify_all_incidents(world.store)

    assert "manifiesto incident-record sin registro" in "\n".join(found[ID])
