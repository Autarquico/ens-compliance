"""El motor (`checks run`) lee la evidencia del agente osquery y el registro, con las mismas
garantías que la del script: manifiestos verificados, huella pinneada y cierre seguro."""
# ruff: noqa: F811

from datetime import timedelta

import pytest
import yaml

from ens_evidence.agent.registry import REGISTRY_KEY
from ens_evidence.agent.results import (
    Snapshot,
    contact_result,
    scheduled_result,
    write_result,
)
from ens_evidence.checks.inputs import evidence_after_run
from ens_evidence.endpoint.agent_view import load_admissions
from ens_evidence.evidence.layout import manifest_key
from ens_evidence.evidence.manifests import load_manifest
from ens_evidence.matrix.evidence import manifest_sha256
from ens_evidence.models.enums import CheckStatus

from .test_engine import NOW, evaluate, store, write_config  # noqa: F401
from .test_inputs import rewrite_manifest

UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
SERVER = "srv-app"
CONFIG = {
    "max_evidence_age_days": 30,
    "supported_os": [{"platform": "linux", "name": "Debian GNU/Linux", "release": "12"}],
    "hosts": {SERVER: {"hardware_uuid": UUID, "hostname": "srv-app.taller-norte.example"}},
}
EPOCH = str(int((NOW - timedelta(days=1)).timestamp()))
LINUX_ROWS = {
    "os_version": [{"name": "Debian GNU/Linux", "platform": "debian", "version": "12.5"}],
    "osquery_info": [{"version": "5.14.1"}],
    "root_mount": [{"device": "/dev/mapper/vg-root", "path": "/"}],
    "disk_encryption": [{"name": "/dev/mapper/vg-root", "encrypted": "1"}],
    "deb_packages": [{"name": "openssl", "version": "3.0", "status": "install ok installed"}],
    "rpm_packages": [],
    "clamav_signatures": [{"path": "/var/lib/clamav/daily.cvd", "mtime": EPOCH, "size": "100"}],
    "clamav_units": [{"id": "clamav-freshclam.service", "active_state": "active"}],
}
SEEN = NOW - timedelta(hours=1)


def register(store, generation=1):
    record = {"generation": generation, "last_admission_at": (NOW - timedelta(days=3)).isoformat()}
    store.write_text(REGISTRY_KEY, yaml.safe_dump({"agents": {SERVER: record}}))


def deliver_results(store, at=SEEN):
    snapshots = [Snapshot(query, rows, at) for query, rows in LINUX_ROWS.items()]
    result = scheduled_result(SERVER, UUID, "linux", snapshots)
    return write_result(store, result, now=at)


def deliver_contact(store, at=SEEN, generation=1):
    result = contact_result(SERVER, UUID, "linux", generation, now=at)
    return write_result(store, result, now=at)


@pytest.fixture
def agent_store(store):
    write_config(store, "endpoint", CONFIG)
    register(store)
    return store


def host(run, check_id=None):
    mine = [r for r in run.results if r.asset_id == SERVER]
    return (
        {r.check_id: r for r in mine}
        if check_id is None
        else next(r for r in mine if r.check_id == check_id)
    )


def test_agent_only_linux_host_is_evaluated_and_its_inputs_are_pinned(agent_store):
    results_id = deliver_results(agent_store)
    contact_id = deliver_contact(agent_store)

    run = evaluate(agent_store, "endpoint")

    mine = host(run)
    assert mine["HOST-001"].status is CheckStatus.PASS
    assert mine["HOST-002"].status is CheckStatus.PASS
    assert mine["HOST-003"].status is CheckStatus.PASS
    assert {i.evidence_id for i in run.inputs} == {results_id, contact_id}
    for item in run.inputs:
        manifest = load_manifest(agent_store, manifest_key(item.evidence_id))
        assert item.manifest_sha256 == manifest_sha256(manifest)
    assert run.complete


def test_a_newer_agent_contact_after_the_run_is_reported_as_missing_run(agent_store):
    deliver_results(agent_store)
    deliver_contact(agent_store)
    run = evaluate(agent_store, "endpoint")
    assert evidence_after_run(agent_store, "endpoint", run, NOW) is None

    deliver_contact(agent_store, at=NOW - timedelta(minutes=5))

    assert evidence_after_run(agent_store, "endpoint", run, NOW) == "check_run_missing"


@pytest.mark.parametrize("kind", ["results", "contact"])
def test_tampered_agent_evidence_closes_every_check_unknown(agent_store, kind):
    results_id = deliver_results(agent_store)
    contact_id = deliver_contact(agent_store)
    rewrite_manifest(
        agent_store,
        results_id if kind == "results" else contact_id,
        r"^asset_id: .*$",
        "asset_id: lap-otro",
    )

    run = evaluate(agent_store, "endpoint")

    assert {r.status for r in run.results} == {CheckStatus.UNKNOWN}
    assert run.inputs == []


def test_agent_evidence_disguised_by_its_manifest_source_closes_unknown(agent_store):
    results_id = deliver_results(agent_store)
    deliver_contact(agent_store)
    rewrite_manifest(agent_store, results_id, r"^source: .*$", "source: otra-cosa")

    run = evaluate(agent_store, "endpoint")

    assert {r.status for r in run.results} == {CheckStatus.UNKNOWN}


def test_a_modified_agent_artifact_closes_every_check_unknown(agent_store):
    deliver_results(agent_store)
    contact_id = deliver_contact(agent_store)
    manifest = load_manifest(agent_store, manifest_key(contact_id))
    agent_store.write_text(manifest.artifact.key, '{"alterado": true}')

    run = evaluate(agent_store, "endpoint")

    assert {r.status for r in run.results} == {CheckStatus.UNKNOWN}


def test_contact_older_than_the_threshold_fails_host_002(agent_store):
    deliver_results(agent_store)
    deliver_contact(agent_store, at=NOW - timedelta(hours=30))

    run = evaluate(agent_store, "endpoint")

    assert host(run, "HOST-002").status is CheckStatus.FAIL
    assert host(run, "HOST-002").reason_code == "agent_silent"


@pytest.mark.parametrize("content", ["agents: [esto no es un mapa", "agents:\n  srv-app: {x: 1}\n"])
def test_unreadable_registry_leaves_host_002_unknown_without_crashing(agent_store, content):
    deliver_results(agent_store)
    deliver_contact(agent_store)
    agent_store.write_text(REGISTRY_KEY, content)

    run = evaluate(agent_store, "endpoint")

    assert host(run, "HOST-002").status is CheckStatus.UNKNOWN
    assert CheckStatus.PASS not in {r.status for r in run.results}
    assert run.errors == []


def test_without_a_registry_entry_host_002_is_unknown_no_agent(store):
    write_config(store, "endpoint", CONFIG)
    deliver_results(store)
    deliver_contact(store)

    run = evaluate(store, "endpoint")

    assert host(run, "HOST-002").status is CheckStatus.UNKNOWN
    assert host(run, "HOST-002").reason_code == "no_agent"


# --- el registro de altas queda fijado en el intento -------------------------------------------


def after_run(store, run):
    return evidence_after_run(store, "endpoint", run, NOW)


def pinned_run(store):
    deliver_results(store)
    deliver_contact(store)
    run = evaluate(store, "endpoint")
    assert run.registry_sha256 is not None
    assert after_run(store, run) is None
    return run


def test_readmitting_without_new_evidence_is_drift(agent_store):
    run = pinned_run(agent_store)

    register(agent_store, generation=2)

    assert after_run(agent_store, run) == "check_run_missing"


def test_revoking_without_new_evidence_is_drift(agent_store):
    run = pinned_run(agent_store)
    record = {"generation": 1, "revoked_at": NOW.isoformat()}
    agent_store.write_text(REGISTRY_KEY, yaml.safe_dump({"agents": {SERVER: record}}))

    assert after_run(agent_store, run) == "check_run_missing"


def test_an_unrelated_registry_edit_is_not_drift(agent_store):
    run = pinned_run(agent_store)
    record = {"generation": 1, "last_admission_at": (NOW - timedelta(days=3)).isoformat()}
    other = {"generation": 4, "last_admission_at": NOW.isoformat(), "revoked_at": NOW.isoformat()}
    agent_store.write_text(
        REGISTRY_KEY, yaml.safe_dump({"agents": {SERVER: record, "lap-x": other}})
    )

    assert after_run(agent_store, run) is None


def test_a_run_stored_without_a_pinned_registry_counts_as_drift(agent_store):
    run = pinned_run(agent_store)

    assert after_run(agent_store, run.model_copy(update={"registry_sha256": None})) == (
        "check_run_missing"
    )


def test_an_unreadable_registry_blocks(agent_store):
    run = pinned_run(agent_store)

    agent_store.write_text(REGISTRY_KEY, "agents: [roto")

    assert after_run(agent_store, run) == "evidence_unreadable"


def test_without_endpoint_config_agent_evidence_is_not_used(store):
    deliver_results(store)
    deliver_contact(store)
    register(store)
    run = evaluate(store, "endpoint")
    run = run.model_copy(update={"registry_sha256": load_admissions(store).sha256})

    assert after_run(store, run) is None
