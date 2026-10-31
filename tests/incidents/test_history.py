import json
from datetime import timedelta

import pytest

from ens_evidence.evidence import load_manifest_by_id
from ens_evidence.evidence.importer import INCIDENT_HISTORY_SOURCE, import_generated
from ens_evidence.evidence.layout import manifest_key
from ens_evidence.incidents import (
    IncidentRejected,
    check_history,
    record_history,
    verify_history,
    verify_incident,
)
from ens_evidence.incidents.history import IncidentHistory, render_history
from ens_evidence.incidents.verify import scan_incident

from .helpers import RECORDED
from .world import BY, World

ID = "INC-2026-001"
CLOSED_AT = RECORDED + timedelta(hours=2)


def history_of(world: World, incident_id: str = ID) -> IncidentHistory:
    manifest = scan_incident(world.store, incident_id).histories[0]
    return IncidentHistory.model_validate_json(world.store.read_bytes(manifest.artifact.key))


def forged_history(world: World, history: IncidentHistory, incident_id: str = ID):
    return import_generated(
        world.store,
        render_history(history),
        source=INCIDENT_HISTORY_SOURCE,
        asset_id=f"incident:{incident_id}",
        submitted_by=BY,
        now=CLOSED_AT,
        collected_at=CLOSED_AT,
    )


@pytest.fixture
def closed(world):
    recorded = world.closed_incident()
    return load_manifest_by_id(world.store, recorded.history_evidence_id)


def test_closing_generates_the_canonical_history_with_the_exact_records(world):
    recorded = world.closed_incident()

    manifest = load_manifest_by_id(world.store, recorded.history_evidence_id)
    assert manifest.source == "incident-history"
    assert manifest.asset_id == f"incident:{ID}"
    assert manifest.collected_at == CLOSED_AT
    assert manifest.valid_until is None
    assert manifest.provenance.kind == "manual"
    data = world.store.read_bytes(manifest.artifact.key)
    document = json.loads(data)
    assert (
        data
        == json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    )
    scan = scan_incident(world.store, ID)
    assert document["simulated"] is False
    assert document["final_status"] == "closed"
    assert document["records"] == [
        {
            "seq": s.record.seq,
            "key": s.key,
            "sha256": s.sha256,
            "evidence_id": scan.evidence[s.sha256].id,
        }
        for s in scan.records
    ]
    assert verify_history(world.store, manifest) == []
    assert verify_incident(world.store, ID) == []


def test_a_simulation_history_says_so(world, monkeypatch):
    world.approve_procedure(monkeypatch)
    procedure = world.procedure().model_dump(mode="json")
    world.open(id="SIM-2026-001", simulated=True, procedure=procedure)
    recorded = world.close()

    manifest = load_manifest_by_id(world.store, recorded.history_evidence_id)
    assert history_of(world, "SIM-2026-001").simulated is True
    assert verify_history(world.store, manifest) == []


def test_omitted_and_extra_records_invalidate_the_history(world, closed):
    real = history_of(world)
    extra = real.records[-1].model_copy(update={"seq": 99})
    tampered = real.model_copy(update={"records": [*real.records[1:], extra]})

    problems = "\n".join(verify_history(world.store, forged_history(world, tampered)))

    assert "omite el registro 1" in problems
    assert "incluye el registro 99, que no existe" in problems


def test_a_record_that_differs_from_the_real_one_invalidates_the_history(world, closed):
    real = history_of(world)
    wrong = real.records[1].model_copy(update={"sha256": "e" * 64})
    tampered = real.model_copy(update={"records": [real.records[0], wrong, real.records[2]]})

    problems = "\n".join(verify_history(world.store, forged_history(world, tampered)))

    assert "el registro 2 del historial no coincide con el real" in problems


def test_a_history_without_closure_is_invalid(world):
    world.open()
    world.update()
    scan = scan_incident(world.store, ID)
    pretend = IncidentHistory(
        incident_id=ID,
        simulated=False,
        final_status="closed",
        records=[
            {"seq": s.record.seq, "key": s.key, "sha256": s.sha256, "evidence_id": "EV-1"}
            for s in scan.records
        ],
    )

    problems = "\n".join(verify_history(world.store, forged_history(world, pretend)))

    assert "único cierre" in problems
    assert "no está cerrado" in problems


def test_a_substituted_history_artifact_is_invalid(world, closed):
    world.store.write_bytes(closed.artifact.key, b'{"records":[]}')

    assert "el artefacto ha cambiado" in "\n".join(verify_history(world.store, closed))


def test_a_second_history_for_the_incident_invalidates_both(world, closed):
    other = forged_history(world, history_of(world))

    # mismo contenido, otro manifiesto: el artefacto se comparte pero hay dos historiales
    assert "más de un incident-history" in "\n".join(verify_history(world.store, closed))
    assert "más de un incident-history" in "\n".join(verify_history(world.store, other))


def test_an_unreadable_or_non_canonical_history_is_invalid(world, closed):
    real = history_of(world)
    garbage = import_generated(
        world.store,
        b"no es json",
        source=INCIDENT_HISTORY_SOURCE,
        asset_id=f"incident:{ID}",
        submitted_by=BY,
        now=CLOSED_AT,
        collected_at=CLOSED_AT,
    )
    pretty = import_generated(
        world.store,
        real.model_dump_json(indent=2).encode(),
        source=INCIDENT_HISTORY_SOURCE,
        asset_id=f"incident:{ID}",
        submitted_by=BY,
        now=CLOSED_AT,
        collected_at=CLOSED_AT,
    )

    assert "no es JSON válido" in "\n".join(verify_history(world.store, garbage))
    assert "no está en JSON canónico" in "\n".join(verify_history(world.store, pretty))


def test_a_tampered_record_invalidates_the_history(world, closed):
    key = scan_incident(world.store, ID).records[1].key
    world.store.write_text(key, world.store.read_text(key) + "\n")

    assert "la clave no coincide con el contenido" in "\n".join(verify_history(world.store, closed))


def test_a_deleted_record_invalidates_the_history(world, closed):
    world.remove(scan_incident(world.store, ID).records[-1].key)

    assert "sin registro" in "\n".join(verify_history(world.store, closed))


def test_a_broken_cited_evidence_invalidates_the_history(world):
    evidence_id = world.evidence()
    world.open(evidence_ids=[evidence_id])
    recorded = world.close()
    manifest = load_manifest_by_id(world.store, recorded.history_evidence_id)
    assert verify_history(world.store, manifest) == []

    world.store.write_bytes(load_manifest_by_id(world.store, evidence_id).artifact.key, b"x")

    assert "referencia EV rota" in "\n".join(verify_history(world.store, manifest))


def test_a_history_of_another_source_or_asset_is_rejected(world, closed):
    other = closed.model_copy(update={"source": "manual"})
    assert "no es una evidencia incident-history" in verify_history(world.store, other)[0]
    elsewhere = closed.model_copy(update={"asset_id": "a1"})
    assert "el activo no es un incidente" in verify_history(world.store, elsewhere)[0]


def test_record_history_needs_a_closed_incident_without_history(world, closed):
    assert (
        "ya tiene historial"
        in pytest.raises(
            IncidentRejected, record_history, world.store, ID, by=BY, now=CLOSED_AT
        ).value.errors[0]
    )

    other = World(world.root.parent / "otro")
    other.open()
    assert (
        "no está cerrado"
        in pytest.raises(
            IncidentRejected, record_history, other.store, ID, by=BY, now=CLOSED_AT
        ).value.errors[0]
    )


def test_a_closure_without_history_is_reported_and_can_be_completed(world):
    world.open()
    closed = world.close()
    world.remove(manifest_key(closed.history_evidence_id))

    assert "cerrado sin evidencia incident-history" in "\n".join(verify_incident(world.store, ID))

    manifest = record_history(world.store, ID, by=BY, now=CLOSED_AT)

    assert verify_incident(world.store, ID) == []
    assert verify_history(world.store, manifest) == []


def test_check_history_reports_whether_it_is_a_simulation(world, monkeypatch):
    world.approve_procedure(monkeypatch)
    world.open(
        id="SIM-2026-001", simulated=True, procedure=world.procedure().model_dump(mode="json")
    )
    closed = world.close()
    manifest = load_manifest_by_id(world.store, closed.history_evidence_id)

    checked = check_history(world.store, manifest)

    assert checked.problems == [] and checked.simulated is True


def test_an_invalid_incident_asset_is_a_problem_not_a_crash(world):
    world.open()
    closed = world.close()
    manifest = load_manifest_by_id(world.store, closed.history_evidence_id)
    forged = manifest.model_copy(update={"asset_id": "incident:../x"})

    checked = check_history(world.store, forged)

    assert any("el activo no es un incidente válido" in problem for problem in checked.problems)
