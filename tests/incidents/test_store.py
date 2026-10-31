from datetime import timedelta

import pytest
import yaml
from typer.testing import CliRunner

from ens_evidence.cli import app
from ens_evidence.evidence import list_manifests
from ens_evidence.evidence.importer import (
    GENERATED_SOURCES,
    INCIDENT_HISTORY_SOURCE,
    import_generated,
)
from ens_evidence.evidence.layout import manifest_key, sha256_hex
from ens_evidence.incidents import (
    IncidentRejected,
    RecordKind,
    next_incident_id,
    record_incident,
    verify_incident,
)
from ens_evidence.incidents.layout import seq_key
from ens_evidence.workspace import AlreadyExists, LocalStore, StorageUnavailable

from .helpers import RECORDED
from .world import BY, World

ID = "INC-2026-001"


def rejection(call) -> IncidentRejected:
    with pytest.raises(IncidentRejected) as raised:
        call()
    return raised.value


def test_opening_writes_reservation_manifest_artifact_and_record(world):
    recorded = world.open()

    keys = world.keys()
    assert seq_key(ID, 1) in keys
    assert recorded.key in keys
    manifest = list_manifests(world.store).manifests[0]
    assert (manifest.id, manifest.source, manifest.asset_id) == (
        recorded.evidence_id,
        "incident-record",
        f"incident:{ID}",
    )
    assert manifest.provenance.kind == "manual"
    assert manifest.collected_at == manifest.provenance.submitted_at == RECORDED
    assert manifest.valid_until is None
    data = world.store.read_bytes(recorded.key)
    assert manifest.artifact.sha256 == sha256_hex(data)
    assert recorded.key.endswith(f"-{sha256_hex(data)[:12]}.yaml")
    assert verify_incident(world.store, ID) == []


def test_record_is_canonical_yaml_and_chain_links_previous_bytes(world):
    first = world.open()
    second = world.update(analysis={"summary": "Credenciales reutilizadas.", "recommendations": []})

    assert second.record.seq == 2
    assert second.record.previous_sha256 == sha256_hex(world.store.read_bytes(first.key))
    text = world.store.read_text(second.key)
    assert list(yaml.safe_load(text)) == sorted(yaml.safe_load(text))
    assert verify_incident(world.store, ID) == []


def test_next_incident_id_counts_real_and_simulated_apart(world):
    assert next_incident_id(world.store, simulated=False, year=2026) == "INC-2026-001"
    world.open()
    assert next_incident_id(world.store, simulated=False, year=2026) == "INC-2026-002"
    assert next_incident_id(world.store, simulated=True, year=2026) == "SIM-2026-001"
    assert next_incident_id(world.store, simulated=False, year=2027) == "INC-2027-001"


def test_privacy_rejection_leaves_no_reservation_record_artifact_or_manifest(world):
    error = rejection(lambda: world.open(title="Aviso de ana@ejemplo.invalid"))

    assert error.errors
    assert "ana@ejemplo.invalid" not in " ".join(error.errors)
    assert world.written() == []


def test_model_rejection_leaves_nothing(world):
    error = rejection(lambda: world.open(detected_at=RECORDED + timedelta(hours=1)))
    assert "detected_at" in error.errors[0]
    assert world.written() == []


def test_blank_author_is_rejected_before_writing(world):
    error = rejection(
        lambda: record_incident(
            world.store, world.new_state(), kind=RecordKind.OPENED, by=" ", now=RECORDED
        )
    )
    assert "--by" in error.errors[0]
    assert world.written() == []


def test_opening_twice_and_updating_a_missing_incident_are_rejected(world):
    missing = rejection(
        lambda: record_incident(
            world.store, world.new_state(), kind=RecordKind.UPDATED, by=BY, now=RECORDED
        )
    )
    assert "no existe" in missing.errors[0]
    world.open()
    assert "ya existe" in rejection(world.open).errors[0]


def test_without_declared_levels_severity_is_null_with_a_warning(world):
    recorded = world.open()

    assert recorded.record.state.severity is None
    assert any("severity: null" in warning for warning in recorded.warnings)


def test_severity_without_declared_levels_is_rejected(world):
    error = rejection(lambda: world.open(severity="alto"))

    assert "severity" in error.errors[0]
    assert world.written() == []


def test_undeclared_severity_is_rejected_and_names_the_levels_not_the_value(world):
    world.declare_levels()
    error = rejection(lambda: world.open(severity="crítico"))

    assert "bajo, alto" in error.errors[0]
    assert "crítico" not in error.errors[0]
    assert world.written() == ["org/answers.yaml"]


def test_declared_severity_is_stored_with_the_declared_spelling(world):
    world.declare_levels()
    recorded = world.open(severity="ALTO")

    assert recorded.record.state.severity == "alto"
    assert not any("severity" in warning for warning in recorded.warnings)


def test_unchanged_severity_is_not_revalidated_when_levels_change(world):
    world.declare_levels()
    world.open(severity="alto")
    world.declare_levels(({"nivel": "grave", "criterio": "x y", "escalado": "z w"},))

    assert world.update().record.state.severity == "alto"


def test_real_incident_without_approved_procedure_is_recorded_with_a_warning(world):
    recorded = world.open()

    assert recorded.record.state.procedure is None
    assert any("procedimiento-incidentes" in warning for warning in recorded.warnings)


def test_real_incident_takes_the_current_approved_procedure(world):
    world.approve_procedure()
    recorded = world.open()

    procedure = recorded.record.state.procedure
    assert procedure == world.procedure()
    assert procedure.approval_key.startswith("org/docs/approvals/procedimiento-incidentes/")
    assert verify_incident(world.store, ID) == []


def test_simulation_without_approved_procedure_is_rejected(world):
    world.approve_procedure()
    procedure = world.procedure().model_dump(mode="json")
    for key in world.keys("org/docs/approvals"):
        world.remove(key)

    error = rejection(lambda: world.open(id="SIM-2026-001", simulated=True, procedure=procedure))

    assert "no está aprobado y vigente" in error.errors[0]
    assert not any(key.startswith("incidents/") for key in world.keys())


def test_simulation_with_a_stale_procedure_is_rejected(world):
    world.approve_procedure()
    stale = {**world.procedure().model_dump(mode="json"), "document_sha256": "c" * 64}

    error = rejection(lambda: world.open(id="SIM-2026-001", simulated=True, procedure=stale))

    assert "no es la aprobación vigente" in error.errors[0]


def test_simulation_with_the_current_procedure_is_recorded(world):
    world.approve_procedure()
    procedure = world.procedure().model_dump(mode="json")

    recorded = world.open(id="SIM-2026-001", simulated=True, procedure=procedure)

    assert recorded.record.state.simulated
    assert verify_incident(world.store, "SIM-2026-001") == []


def test_procedure_cannot_change_after_opening(world):
    world.approve_procedure()
    world.open()
    error = rejection(lambda: world.update(procedure=None))

    assert "procedure es inmutable" in " ".join(error.errors)


def test_cited_evidence_must_verify_when_writing(world):
    error = rejection(lambda: world.open(evidence_ids=["EV-20261015-deadbeef"]))
    assert "evidencia citada rota" in error.errors[0]
    assert world.written() == []

    recorded = world.open(evidence_ids=[world.evidence()])
    assert recorded.record.state.evidence_ids


class RacingStore(LocalStore):
    """Otra orden gana la reserva justo antes de que esta la cree."""

    def create_bytes(self, key: str, data: bytes) -> None:
        if key == self.racing_key:
            super().create_bytes(key, b"otra orden\n")
        super().create_bytes(key, data)


def racing(world: World, key: str) -> None:
    world.store = RacingStore(world.root)
    world.store.racing_key = key


@pytest.mark.parametrize("second", ["update", "close"])
def test_a_lost_reservation_race_fails_and_writes_nothing_else(world, second):
    world.open()
    before = world.keys()
    racing(world, seq_key(ID, 2))

    error = rejection(world.update if second == "update" else world.close)

    assert "reservó antes la secuencia 2" in error.errors[0]
    assert [k for k in world.keys() if k not in before] == [seq_key(ID, 2)]
    assert len(list_manifests(world.store).manifests) == 1


def test_a_closed_incident_accepts_nothing_more(world):
    world.closed_incident()
    before = world.keys()

    error = rejection(world.update)

    assert "no admite más registros" in " ".join(error.errors)
    assert world.keys() == before


class FailingStore(LocalStore):
    def create_bytes(self, key: str, data: bytes) -> None:
        if key.endswith(".yaml") and key.startswith("incidents/"):
            raise StorageUnavailable("disco lleno")
        super().create_bytes(key, data)


def test_a_failure_between_reservation_and_record_leaves_a_blocking_orphan(world):
    world.open()
    world.store = FailingStore(world.root)
    with pytest.raises(StorageUnavailable):
        world.update()
    world.store = LocalStore(world.root)

    assert seq_key(ID, 2) in world.keys()
    problems = " ".join(verify_incident(world.store, ID))
    assert "reserva de la secuencia 2 sin registro" in problems
    assert "manifiesto incident-record sin registro" in problems
    assert "está roto" in rejection(world.update).errors[0]


def test_reservation_is_exclusive(world):
    world.open()
    with pytest.raises(AlreadyExists):
        world.store.create_bytes(seq_key(ID, 1), b"x")


def test_generated_sources_are_only_for_the_tool(world):
    assert GENERATED_SOURCES == {"incident-record", "incident-history"}
    with pytest.raises(ValueError, match="fuente generada"):
        import_generated(
            world.store,
            b"x",
            source="manual",
            asset_id="a",
            submitted_by=BY,
            now=RECORDED,
            collected_at=RECORDED,
        )


@pytest.mark.parametrize("source", sorted(GENERATED_SOURCES))
def test_evidence_import_refuses_generated_sources(tmp_path, source):
    settings = tmp_path / "settings.yaml"
    settings.write_text(
        yaml.safe_dump({"workspace": {"backend": "local", "local": {"path": str(tmp_path / "ws")}}})
    )
    document = tmp_path / "x.txt"
    document.write_bytes(b"x")

    result = CliRunner().invoke(
        app,
        [
            "--settings",
            str(settings),
            "evidence",
            "import",
            str(document),
            "--source",
            source,
            "--asset",
            "a",
            "--by",
            BY,
        ],
    )

    assert result.exit_code != 0
    assert list_manifests(LocalStore(tmp_path / "ws")).manifests == []


def test_a_history_on_an_open_incident_rejects_the_closure_and_writes_nothing(world):
    world.open()
    import_generated(
        world.store,
        b"{}",
        source=INCIDENT_HISTORY_SOURCE,
        asset_id=f"incident:{ID}",
        submitted_by=BY,
        now=RECORDED,
        collected_at=RECORDED,
    )
    before = world.keys()

    error = rejection(world.close)

    assert "no está cerrado" in " ".join(error.errors)
    assert world.keys() == before


def test_a_history_failure_after_the_closure_is_reported_not_rejected(world):
    world.open()
    world.race_history_after_closure()

    recorded = world.close()

    assert recorded.history_evidence_id is None
    assert recorded.history_problems == [f"{ID} ya tiene historial ({world.store.raced.id})"]
    assert recorded.key in world.keys()
    world.drop_raced_history()
    assert "cerrado sin evidencia incident-history" in " ".join(verify_incident(world.store, ID))


def test_a_closed_incident_without_history_points_to_the_recovery_command(world):
    closed = world.closed_incident()
    world.remove(manifest_key(closed.history_evidence_id))

    error = rejection(world.update)

    assert f"incident history {ID}" in " ".join(error.errors)


@pytest.mark.parametrize(
    "by",
    ["ana@ejemplo.invalid", "12345678Z", "AKIAABCDEFGHIJKLMNOP", "x" * 121],
)
def test_the_declared_author_is_minimized_like_any_other_field(world, by):
    error = rejection(
        lambda: record_incident(
            world.store, world.new_state(), kind=RecordKind.OPENED, by=by, now=RECORDED
        )
    )

    assert error.errors and all(e.startswith("--by") for e in error.errors)
    assert by not in " ".join(error.errors)
    assert world.written() == []


def test_an_asset_outside_the_inventory_is_rejected_and_nothing_is_written(world):
    before = world.keys()

    error = rejection(lambda: world.open(assets=["APP-001", "NO-EXISTE"]))

    assert error.errors == ["state.assets[1]: no está en el inventario"]
    assert world.keys() == before


def test_no_error_repeats_an_asset_value_that_looks_like_personal_data(world):
    error = rejection(lambda: world.open(assets=["12345678Z", "APP-001", "600123456"]))

    assert "12345678Z" not in " ".join(error.errors)
    assert "600123456" not in " ".join(error.errors)
    assert not any("inventario" in e and "assets[0]" in e for e in error.errors)
