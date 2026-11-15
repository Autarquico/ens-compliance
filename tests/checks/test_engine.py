import dataclasses
from datetime import UTC, datetime, timedelta

import pytest
import yaml
from matrix.source_helpers import ROW, check_link
from scope.scope_helpers import APPROVED_AT, EXAMPLE_SCOPE, SCOPE_FILES, edit

from ens_evidence import __version__
from ens_evidence.checks import engine
from ens_evidence.checks.engine import (
    CHECK_CRASHED,
    CONFIG_INVALID,
    OutcomeKind,
    evaluate_connector,
    run_all,
)
from ens_evidence.checks.inputs import (
    EVIDENCE_INCONSISTENT,
    EVIDENCE_UNREADABLE,
    EVIDENCE_UNVERIFIED,
    NO_EVIDENCE,
)
from ens_evidence.checks.source import StoredCheckResults
from ens_evidence.checks.store import scan_runs
from ens_evidence.connectors.config import connector_config_key
from ens_evidence.endpoint.checks import HOST_CHECKS
from ens_evidence.evidence.importer import import_attested_package, import_collected
from ens_evidence.evidence.layout import CHECKS_PREFIX, sha256_hex
from ens_evidence.matrix.evidence import manifest_sha256
from ens_evidence.matrix.technical import technical_support
from ens_evidence.models.contracts import CollectionResult
from ens_evidence.models.enums import CheckStatus, CollectionStatus, Role
from ens_evidence.models.readiness import BlockerKind
from ens_evidence.scope import ScopeNotApproved, approve_scope, load_inventory, scope_status
from ens_evidence.workspace import LocalStore

from .test_expected import COLLECTED, gh_evidence
from .test_inputs import rewrite_manifest

NOW = datetime(2026, 10, 30, 12, tzinfo=UTC)
LIVE = {"facturas": False, "infra": False}
GITHUB_CONFIG = {"asset_id": "gh-taller", "organization": "taller-norte"}
SECRET = "github.secret_scanning_enabled"
BRANCH = "github.default_branch_protected"
GITHUB_CHECKS = 4
GITHUB_RESULTS = 1 + 3 * 2  # la organización y tres checks por dos repositorios


@pytest.fixture
def store(tmp_path):
    workspace = LocalStore(tmp_path / "ws")
    for name in SCOPE_FILES:
        workspace.write_text(
            f"scope/{name}.yaml", (EXAMPLE_SCOPE / f"{name}.yaml").read_text(encoding="utf-8")
        )
    approve_scope(workspace, "Ana Ruiz", Role.SYSTEM_OWNER, APPROVED_AT)
    write_config(workspace, "github", GITHUB_CONFIG)
    return workspace


def write_config(store, name, data):
    store.write_text(connector_config_key(name), yaml.safe_dump(data))


def inventory(store):
    loaded = load_inventory(store)
    assert loaded.inventory is not None
    return loaded.inventory


def collect(store, repos=LIVE, *, at=COLLECTED, **changes):
    evidence = gh_evidence(repos, collected_at=at).model_copy(update=changes)
    return import_collected(store, evidence, collector_version="1", now=at + timedelta(minutes=1))


def evaluate(store, connector="github", now=NOW):
    return evaluate_connector(store, connector, inventory(store), now)


def run_files(store):
    return store.list(CHECKS_PREFIX)


def by_check(run):
    found: dict[str, list] = {}
    for result in run.results:
        found.setdefault(result.check_id, []).append(result)
    return found


def crash_in(monkeypatch, connector, check_id, message="token-secreto-123"):
    spec = engine.ENGINE_CONNECTORS[connector]

    def boom(*_):
        raise RuntimeError(message)

    patched = dataclasses.replace(spec, checks={**spec.checks, check_id: boom})
    monkeypatch.setattr(
        engine, "ENGINE_CONNECTORS", {**engine.ENGINE_CONNECTORS, connector: patched}
    )


# --- una ejecución sana ------------------------------------------------------------------------


def test_healthy_github_run_is_complete_with_all_the_context(store):
    manifest = collect(store)

    run = evaluate(store)

    assert run.complete and run.errors == []
    assert len(run.results) == GITHUB_RESULTS
    assert {r.status for r in run.results} == {CheckStatus.PASS}
    assert run.seq == 1
    assert run.connector == "github"
    assert run.engine_version == __version__
    assert run.started_at == run.finished_at == NOW
    assert [(i.evidence_id, i.manifest_sha256) for i in run.inputs] == [
        (manifest.id, manifest_sha256(manifest))
    ]
    assert run.config_sha256 == sha256_hex(store.read_bytes(connector_config_key("github")))
    assert run.scope_fingerprint == scope_status(store).fingerprint
    assert run.expected_subjects["github.org_2fa_required"] == ["taller-norte"]
    assert run.expected_subjects[SECRET] == ["facturas", "infra"]


def test_evaluating_alone_writes_nothing(store):
    collect(store)
    before = store.list("")

    evaluate(store)

    assert store.list("") == before


def test_results_cite_the_evidence_they_were_computed_from(store):
    manifest = collect(store)

    run = evaluate(store)

    assert all(r.evidence_ids == [manifest.id] for r in run.results)


# --- cierre seguro -----------------------------------------------------------------------------


def break_nothing(store):
    return None


def break_manifest(store):
    store.write_text("evidence/manifests/EV-20261030-deadbeef.yaml", "id: [roto")


def break_artifact(store):
    newest = collect(store, at=COLLECTED + timedelta(hours=1))
    store.write_bytes(newest.artifact.key, b"{}")


def break_identity(store):
    newest = collect(store, at=COLLECTED + timedelta(hours=1))
    rewrite_manifest(
        store, newest.id, r"^collected_at: .*$", "collected_at: '2026-10-30T08:00:00+00:00'"
    )


SAFE_CLOSED = [
    (NO_EVIDENCE, break_nothing, False),
    (EVIDENCE_UNREADABLE, break_manifest, True),
    (EVIDENCE_UNVERIFIED, break_artifact, True),
    (EVIDENCE_INCONSISTENT, break_identity, True),
]


@pytest.mark.parametrize(("code", "damage", "had_good_evidence"), SAFE_CLOSED)
def test_input_failure_closes_safe_and_never_falls_back_to_older_evidence(
    store, code, damage, had_good_evidence
):
    if had_good_evidence:
        collect(store, at=COLLECTED - timedelta(days=1))
    damage(store)

    run = evaluate(store)

    assert len(run.results) == GITHUB_RESULTS
    assert {r.status for r in run.results} == {CheckStatus.UNKNOWN}
    assert {r.reason_code for r in run.results} == {code}
    assert all(r.evidence_ids == [] for r in run.results)
    assert run.inputs == []
    assert run.complete and run.errors == []
    assert run.expected_subjects[SECRET] == ["facturas", "infra"]


def test_one_unknown_per_expected_subject_when_the_input_fails(store):
    run = evaluate(store)

    found = by_check(run)
    assert [r.subject for r in found["github.org_2fa_required"]] == ["taller-norte"]
    for check_id in (SECRET, BRANCH, "github.dependabot_alerts_enabled"):
        assert sorted(r.subject for r in found[check_id]) == ["facturas", "infra"]
        assert {r.asset_id for r in found[check_id]} == {"gh-taller"}


def test_asset_missing_from_the_inventory_closes_safe(store):
    collect(store)
    write_config(store, "github", {**GITHUB_CONFIG, "asset_id": "no-existe"})

    run = evaluate(store)

    assert {r.status for r in run.results} == {CheckStatus.UNKNOWN}
    assert {r.reason_code for r in run.results} == {"inventory_asset_invalid"}
    assert {r.asset_id for r in run.results} == {"no-existe"}


def test_connector_without_inventory_subjects_closes_with_a_single_unknown(store):
    write_config(
        store,
        "cloudflare",
        {"asset_id": "cf-taller", "account_id": "0123456789abcdef0123456789abcdef"},
    )

    run = evaluate(store, "cloudflare")

    assert [(r.check_id, r.subject, r.asset_id, r.status, r.reason_code) for r in run.results] == [
        ("cloudflare.tunnel_requires_access", None, "cf-taller", CheckStatus.UNKNOWN, NO_EVIDENCE)
    ]


def test_invalid_config_gives_an_incomplete_run_without_results(store):
    collect(store)
    store.write_text(connector_config_key("github"), "asset_id: [")

    run = evaluate(store)

    assert not run.complete
    assert run.results == []
    assert [e.split(":")[0] for e in run.errors] == [CONFIG_INVALID]
    assert run.config_sha256 == sha256_hex(b"asset_id: [")


# --- check_crashed -----------------------------------------------------------------------------


def test_check_that_raises_gives_unknown_check_crashed_without_the_message(store, monkeypatch):
    collect(store)
    crash_in(monkeypatch, "github", SECRET)

    run = evaluate(store)

    crashed = by_check(run)[SECRET]
    assert sorted(r.subject for r in crashed) == ["facturas", "infra"]
    assert {r.status for r in crashed} == {CheckStatus.UNKNOWN}
    assert {r.reason_code for r in crashed} == {CHECK_CRASHED}
    assert all("RuntimeError" in r.reason for r in crashed)
    assert run.errors == [f"{CHECK_CRASHED}:{SECRET}:RuntimeError"]
    assert not run.complete
    assert "token-secreto-123" not in run.model_dump_json()


def test_a_crash_does_not_stop_the_other_checks(store, monkeypatch):
    collect(store)
    crash_in(monkeypatch, "github", SECRET)

    others = [r for r in evaluate(store).results if r.check_id != SECRET]

    assert len(others) == GITHUB_RESULTS - 2
    assert {r.status for r in others} == {CheckStatus.PASS}


def test_pass_then_exception_stores_the_attempt_and_the_matrix_keeps_no_pass(store, monkeypatch):
    collect(store)
    source = StoredCheckResults(reference_now=NOW + timedelta(days=1))
    run_all(store, NOW)
    runs = source.runs(store, NOW + timedelta(minutes=30))
    before = technical_support(
        store,
        ROW,
        check_link(BRANCH),
        runs,
        NOW + timedelta(minutes=30),
        source.connector_problems(store, NOW),
    )
    assert before.counts

    crash_in(monkeypatch, "github", BRANCH)
    later = NOW + timedelta(hours=1)
    outcome = next(o for o in run_all(store, later) if o.connector == "github")

    assert outcome.kind is OutcomeKind.RECORDED
    stored = scan_runs(store, "github").runs["github"]
    assert [s.run.complete for s in stored] == [True, False]
    at = later + timedelta(minutes=30)
    after = technical_support(
        store,
        ROW,
        check_link(BRANCH),
        source.runs(store, at),
        at,
        source.connector_problems(store, at),
    )
    assert not after.counts
    assert (BlockerKind.MISSING_EVIDENCE, "check_last_run_failed:github") in {
        (b.kind, b.detail) for b in after.blockers
    }


# --- GitHub: archivados ------------------------------------------------------------------------


def test_archived_repo_leaves_the_three_repository_checks_but_not_the_org_check(store):
    collect(store, {"facturas": False, "infra": True})

    run = evaluate(store)

    found = by_check(run)
    assert [r.subject for r in found["github.org_2fa_required"]] == ["taller-norte"]
    for check_id in (SECRET, BRANCH, "github.dependabot_alerts_enabled"):
        assert run.expected_subjects[check_id] == ["facturas"]
        assert [r.subject for r in found[check_id]] == ["facturas"]
    assert run.complete


@pytest.mark.parametrize("check_id", [SECRET, BRANCH, "github.dependabot_alerts_enabled"])
def test_archive_transition_changes_the_expectation_of_each_check(store, check_id):
    collect(store, LIVE, at=COLLECTED)
    live = evaluate(store)
    collect(store, {"facturas": False, "infra": True}, at=COLLECTED + timedelta(hours=1))
    archived = evaluate(store)

    assert live.expected_subjects[check_id] == ["facturas", "infra"]
    assert archived.expected_subjects[check_id] == ["facturas"]
    assert {r.subject for r in archived.results if r.check_id == check_id} == {"facturas"}


def test_repo_spelling_in_the_inventory_does_not_make_the_run_incomplete_for_the_matrix(store):
    edit(store, "assets", "repos: [facturas, infra]", "repos: [Facturas, INFRA]")
    approve_scope(store, "Ana Ruiz", Role.SYSTEM_OWNER, APPROVED_AT)
    collect(store)

    run = evaluate(store)

    assert run.expected_subjects[SECRET] == ["facturas", "infra"]
    assert {r.subject for r in run.results if r.check_id == SECRET} == {"facturas", "infra"}


def test_organization_check_is_expected_and_produced_on_the_organization(store):
    collect(store)

    run = evaluate(store)

    org = by_check(run)["github.org_2fa_required"]
    assert [(r.subject, r.status) for r in org] == [("taller-norte", CheckStatus.PASS)]
    assert run.expected_subjects["github.org_2fa_required"] == ["taller-norte"]


# --- endpoint ----------------------------------------------------------------------------------

ENDPOINT_CONFIG = {
    "max_evidence_age_days": 30,
    "supported_os": [{"platform": "darwin", "name": "macOS", "release": "15"}],
    "hosts": {"lap-ana": {"hardware_uuid": "uuid-lap-ana", "hostname": "lap-ana.test"}},
}


def import_host(store, asset_id="lap-ana", at=COLLECTED):
    result = CollectionResult(
        source="endpoint-script",
        collector="endpoint-script",
        collected_at=at,
        asset_id=asset_id,
        collection_status=CollectionStatus.SUCCESS,
        data={
            "package": {
                "platform": "darwin",
                "hardware_uuid": f"uuid-{asset_id}",
                "hostname": f"{asset_id}.test",
            },
            "queries": {},
        },
    )
    _, normalized = import_attested_package(
        store,
        f"paquete-{asset_id}-{at:%Y%m%d%H%M}".encode(),
        result,
        package_source="endpoint-package",
        collector_version="1.0",
        submitted_by="ana",
        valid_until=at + timedelta(days=30),
        now=at + timedelta(minutes=1),
    )
    return normalized


@pytest.fixture
def endpoint_store(store):
    write_config(store, "endpoint", ENDPOINT_CONFIG)
    return store


def test_endpoint_evaluates_every_laptop_and_server_of_the_inventory(endpoint_store):
    normalized = import_host(endpoint_store)

    run = evaluate(endpoint_store, "endpoint")

    hosts = ["lap-ana", "lap-personal", "srv-app"]
    assert len(run.results) == len(HOST_CHECKS) * len(hosts) - 1  # sin HOST-009 del servidor
    assert all(run.expected_subjects[c] == hosts for c in HOST_CHECKS if c != "HOST-009")
    assert run.expected_subjects["HOST-009"] == ["lap-ana", "lap-personal"]
    assert [r.subject for r in by_check(run)["HOST-009"]] == ["lap-ana", "lap-personal"]
    coverage = {r.subject: r for r in by_check(run)["HOST-001"]}
    assert coverage["lap-ana"].status is CheckStatus.PASS
    assert coverage["lap-personal"].status is CheckStatus.FAIL
    assert [(i.evidence_id, i.manifest_sha256) for i in run.inputs] == [
        (normalized.id, manifest_sha256(normalized))
    ]
    assert run.complete


def test_endpoint_inputs_are_only_the_latest_evidence_of_each_host(endpoint_store):
    import_host(endpoint_store, at=COLLECTED - timedelta(days=2))
    latest = import_host(endpoint_store)

    run = evaluate(endpoint_store, "endpoint")

    assert [i.evidence_id for i in run.inputs] == [latest.id]


def test_endpoint_evidence_with_a_retouched_identity_closes_every_host_unknown(endpoint_store):
    normalized = import_host(endpoint_store)
    rewrite_manifest(endpoint_store, normalized.id, r"^asset_id: .*$", "asset_id: lap-otro")

    run = evaluate(endpoint_store, "endpoint")

    assert {r.status for r in run.results} == {CheckStatus.UNKNOWN}
    assert {r.reason_code for r in run.results} == {"unreadable_evidence"}
    assert run.inputs == []


def test_endpoint_crash_gives_unknown_check_crashed_for_every_check_and_host(
    endpoint_store, monkeypatch
):
    import_host(endpoint_store)

    def boom(*_):
        raise ValueError("secreto-del-host")

    monkeypatch.setattr(engine, "evaluate_host_checks", boom)

    run = evaluate(endpoint_store, "endpoint")

    assert len(run.results) == len(HOST_CHECKS) * 3 - 1
    assert {r.reason_code for r in run.results} == {CHECK_CRASHED}
    assert {r.status for r in run.results} == {CheckStatus.UNKNOWN}
    assert run.errors == [f"{CHECK_CRASHED}:endpoint:ValueError"]
    assert not run.complete
    assert "secreto-del-host" not in run.model_dump_json()


# --- run_all -----------------------------------------------------------------------------------


def outcomes(store, now=NOW):
    return {o.connector: o for o in run_all(store, now)}


def test_run_all_lists_connectors_without_config_as_not_configured(store):
    collect(store)

    found = outcomes(store)

    assert found["github"].kind is OutcomeKind.RECORDED
    for name in ("cloudflare", "endpoint", "google-workspace", "restic"):
        assert found[name].kind is OutcomeKind.NOT_CONFIGURED
        assert found[name].run is None
    assert list(found) == sorted(found)
    assert len(run_files(store)) == 1


def test_run_all_requires_an_approved_scope(store):
    collect(store)
    edit(store, "system", "  - bk-externo\n", "")

    with pytest.raises(ScopeNotApproved):
        run_all(store, NOW)

    assert run_files(store) == []


def test_run_all_twice_without_changes_does_not_write(store):
    collect(store)
    run_all(store, NOW)
    written = run_files(store)

    second = outcomes(store, NOW + timedelta(hours=3))

    assert second["github"].kind is OutcomeKind.UNCHANGED
    assert second["github"].key is None
    assert run_files(store) == written


def test_run_all_after_a_new_collection_writes_a_new_run(store):
    collect(store)
    run_all(store, NOW)

    collect(store, at=COLLECTED + timedelta(hours=1))
    second = outcomes(store, NOW + timedelta(hours=3))

    assert second["github"].kind is OutcomeKind.RECORDED
    assert second["github"].key in run_files(store)
    assert [s.run.seq for s in scan_runs(store, "github").runs["github"]] == [1, 2]


def test_run_all_records_incomplete_and_input_failures_too(store):
    first = outcomes(store)["github"]

    assert first.kind is OutcomeKind.RECORDED
    assert first.run is not None
    assert {r.reason_code for r in first.run.results} == {NO_EVIDENCE}
    assert len(run_files(store)) == 1


def test_run_all_with_an_unreadable_history_reports_it_and_writes_nothing(store):
    collect(store)
    run_all(store, NOW)
    store.write_bytes(f"{CHECKS_PREFIX}/github/000001-roto.json", b"{}")
    written = run_files(store)

    collect(store, at=COLLECTED + timedelta(hours=1))
    found = outcomes(store, NOW + timedelta(hours=3))["github"]

    assert found.kind is OutcomeKind.HISTORY_UNREADABLE
    assert found.detail
    assert found.key is None
    assert run_files(store) == written
