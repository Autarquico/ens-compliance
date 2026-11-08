import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ens_evidence import schemas
from ens_evidence.agent.registry import (
    REGISTRY_KEY,
    AgentRegistry,
    RegistryError,
    enrolled_asset,
    load_registry,
    record_admission,
    record_revocation,
)
from ens_evidence.endpoint.config import EndpointConfig
from ens_evidence.workspace.cycle_lock import CycleLock

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
HID = "6f1c2d3e-4a5b-4c6d-8e9f-0a1b2c3d4e5f"


@pytest.fixture
def lock(tmp_path):
    with CycleLock("test-registry", tmp_path / "state") as held:
        yield held


def admit(store, lock, asset="laptop-ana"):
    return record_admission(
        store, lock, asset, by="alex", now=NOW, expires_at=NOW + timedelta(hours=24)
    )


def test_empty_registry_when_file_missing(store):
    registry = load_registry(store)
    assert registry.agents == {}
    assert registry.generation_of("laptop-ana") == 1
    assert not registry.accepts("laptop-ana", 1)


def test_admission_is_persisted_and_accepted(store, lock):
    admit(store, lock)
    registry = load_registry(store)
    assert registry.accepts("laptop-ana", 1)
    assert registry.agents["laptop-ana"].last_admission_by == "alex"
    assert store.exists(REGISTRY_KEY)


def test_revocation_bumps_generation_and_rejects_old_keys(store, lock):
    admit(store, lock)
    record = record_revocation(store, lock, "laptop-ana", by="alex", reason="robo", now=NOW)
    registry = load_registry(store)
    assert record.generation == 2 and registry.is_revoked("laptop-ana")
    assert not registry.accepts("laptop-ana", 1)
    assert not registry.accepts("laptop-ana", 2)


def test_readmission_after_revocation_accepts_only_the_new_generation(store, lock):
    admit(store, lock)
    record_revocation(store, lock, "laptop-ana", by="alex", reason="robo", now=NOW)
    admit(store, lock)
    registry = load_registry(store)
    assert registry.generation_of("laptop-ana") == 2
    assert registry.accepts("laptop-ana", 2) and not registry.accepts("laptop-ana", 1)
    assert registry.agents["laptop-ana"].revoked_at is None


def test_registry_is_read_fresh_on_each_call(store, lock):
    assert not load_registry(store).accepts("laptop-ana", 1)
    admit(store, lock)
    assert load_registry(store).accepts("laptop-ana", 1)
    record_revocation(store, lock, "laptop-ana", by="alex", reason="x", now=NOW)
    assert not load_registry(store).accepts("laptop-ana", 1)


def test_writing_requires_the_lock(store, tmp_path):
    free = CycleLock("test-registry-free", tmp_path / "state")
    with pytest.raises(RuntimeError):
        admit(store, free)
    assert not store.exists(REGISTRY_KEY)


@pytest.mark.parametrize("text", ["agents: [", "agents: {a: {generation: 0}}", "extra: 1"])
def test_invalid_registry_raises_without_values(store, text):
    store.write_text(REGISTRY_KEY, text)
    with pytest.raises(RegistryError):
        load_registry(store)


def test_enrolled_asset_matches_uuid_and_hostname():
    config = EndpointConfig.model_validate(
        {
            "max_evidence_age_days": 30,
            "supported_os": [],
            "hosts": {"laptop-ana": {"hardware_uuid": HID.upper(), "hostname": "Ana-MBP"}},
        }
    )
    assert enrolled_asset(config, HID, "ana-mbp") == "laptop-ana"
    assert enrolled_asset(config, HID, "otro") is None
    assert enrolled_asset(config, "11111111-2222-4333-8444-555555555555", "ana-mbp") is None


def test_schema_is_in_sync():
    path = Path(__file__).resolve().parents[2] / "schemas" / "agent-registry.schema.json"
    assert json.loads(path.read_text()) == AgentRegistry.model_json_schema()
    assert "agent-registry" in schemas.CONTRACTS
