from datetime import timedelta

import pytest
from scope.scope_helpers import APPROVED_AT, EXAMPLE_SCOPE, SCOPE_FILES, edit

from ens_evidence.checks.source import StoredCheckResults
from ens_evidence.checks.store import canonical_bytes, record_run, run_key
from ens_evidence.evidence.layout import CHECKS_PREFIX, sha256_hex
from ens_evidence.matrix import CheckResultsSource, CheckRun
from ens_evidence.models.enums import CheckStatus, Role
from ens_evidence.scope import approve_scope, scope_status
from ens_evidence.workspace import LocalStore

from .test_inputs import collect, rewrite_manifest
from .test_store import CHECK, MANIFEST, NOW, result, stored

SOURCE: CheckResultsSource = StoredCheckResults(reference_now=NOW + timedelta(days=30))
EVIDENCE = "EV-20261029-aaaaaaaa"


@pytest.fixture
def store(tmp_path):
    workspace = LocalStore(tmp_path / "ws")
    for name in SCOPE_FILES:
        workspace.write_text(
            f"scope/{name}.yaml", (EXAMPLE_SCOPE / f"{name}.yaml").read_text(encoding="utf-8")
        )
    approve_scope(workspace, "Ana Ruiz", Role.SYSTEM_OWNER, APPROVED_AT)
    return workspace


def config_key(connector="github"):
    return f"connectors/{connector}.yaml"


def record(store, at=NOW, **changes):
    fingerprint = scope_status(store).fingerprint
    connector = changes.get("connector", "github")
    if not store.exists(config_key(connector)):
        store.write_text(config_key(connector), "ejemplo: 1\n")
    config = sha256_hex(store.read_bytes(config_key(connector)))
    run = stored(
        at=at,
        scope_fingerprint=changes.pop("scope_fingerprint", fingerprint),
        config_sha256=changes.pop("config_sha256", config),
        **changes,
    )
    return record_run(store, run, at + timedelta(minutes=1))


def test_without_history_there_are_no_runs_and_no_problems(store):
    assert SOURCE.runs(store, NOW) == []
    assert SOURCE.connector_problems(store, NOW) == {}


def test_runs_come_back_as_check_runs_with_fingerprints_and_expectations(store):
    key = record(store, expected_subjects={CHECK: ["repo-a", "repo-b"]})
    (run,) = SOURCE.runs(store, NOW)
    assert isinstance(run, CheckRun)
    assert run.run_id == key and run.connector == "github" and run.complete
    assert run.evidence_sha256 == {EVIDENCE: MANIFEST}
    assert run.expected_for(CHECK) == {"repo-a", "repo-b"}
    assert run.results[0].status is CheckStatus.PASS
    assert SOURCE.connector_problems(store, NOW) == {}


def test_incomplete_attempt_is_returned_with_its_errors(store):
    record(store, complete=False, errors=["se cortó"])
    (run,) = SOURCE.runs(store, NOW)
    assert not run.complete and run.errors == ("se cortó",)


class TestUnreadableHistory:
    def test_marks_the_connector_with_the_key(self, store):
        key = record(store)
        store.write_bytes(key, b"{}")
        assert SOURCE.connector_problems(store, NOW) == {
            "github": [f"check_history_unreadable:{key}"]
        }

    def test_other_connectors_stay_usable(self, store):
        key = record(store)
        store.write_bytes(key, b"{}")
        record(store, connector="restic", expected_subjects={}, results=[])
        assert "restic" not in SOURCE.connector_problems(store, NOW)
        assert [r.connector for r in SOURCE.runs(store, NOW)] == ["restic"]

    def test_gap_is_reported(self, store):
        record(store)
        middle = record(store, at=NOW + timedelta(1), results=[result(CheckStatus.FAIL)])
        record(store, at=NOW + timedelta(2), config_sha256="d" * 64)
        store._root.joinpath(middle).unlink()
        assert SOURCE.connector_problems(store, NOW) == {
            "github": [f"check_history_unreadable:{CHECKS_PREFIX}/github/seq"]
        }

    def test_unattributable_file_blocks_every_known_connector(self, store):
        stray = f"{CHECKS_PREFIX}/stray.json"
        store.write_bytes(stray, b"{}")
        problems = SOURCE.connector_problems(store, NOW)
        assert problems["github"] == [f"check_history_unreadable:{stray}"]
        assert "restic" in problems and "cloudflare" in problems

    def test_run_stored_under_another_connector_blocks_that_directory(self, store):
        data = canonical_bytes(stored())
        foreign = run_key(stored(connector="restic"), data)
        store.write_bytes(foreign, data)
        assert SOURCE.connector_problems(store, NOW) == {
            "restic": [f"check_history_unreadable:{foreign}"]
        }


class TestClock:
    def write_earlier_attempt(self, store, status, at):
        run = stored(seq=2, results=[result(status)], at=at)
        data = canonical_bytes(run)
        store.write_bytes(run_key(run, data), data)

    def test_a_fail_with_the_clock_set_back_does_not_leave_the_earlier_pass_standing(self, store):
        record(store)
        self.write_earlier_attempt(store, CheckStatus.FAIL, NOW - timedelta(minutes=5))

        problems = SOURCE.connector_problems(store, NOW)

        assert problems["github"] == [f"check_history_unreadable:{CHECKS_PREFIX}/github/order"]
        assert [r for r in SOURCE.runs(store, NOW) if r.connector == "github"] == []

    def test_a_fail_with_the_clock_set_forward_counts_from_its_own_instant(self, store):
        record(store)
        ahead = NOW + timedelta(days=2)
        record(store, at=ahead, results=[result(CheckStatus.FAIL)])

        runs = SOURCE.runs(store, ahead)

        assert max(runs, key=lambda r: r.finished_at).results[0].status is CheckStatus.FAIL
        assert SOURCE.connector_problems(store, ahead) == {}


class TestScope:
    def test_changed_inventory_blocks_the_connector(self, store):
        record(store)
        edit(store, "system", "  - bk-externo\n", "")
        assert SOURCE.connector_problems(store, NOW) == {"github": ["check_scope_changed:github"]}

    def test_run_made_under_another_fingerprint_is_changed(self, store):
        record(store, scope_fingerprint="9" * 64)
        assert SOURCE.connector_problems(store, NOW) == {"github": ["check_scope_changed:github"]}

    def test_unapproved_scope_is_changed(self, store):
        record(store)
        store.write_text(
            "scope/approval.yaml", "revoked: true\nreason: x\nrevoked_at: 2026-10-30T00:00:00Z\n"
        )
        assert SOURCE.connector_problems(store, NOW) == {"github": ["check_scope_changed:github"]}

    def test_only_the_latest_attempt_counts(self, store):
        record(store, scope_fingerprint="9" * 64)
        record(store, at=NOW + timedelta(1), results=[result(CheckStatus.FAIL)])
        assert SOURCE.connector_problems(store, NOW + timedelta(2)) == {}

    def test_a_later_attempt_is_ignored_before_it_happened(self, store):
        record(store)
        record(store, at=NOW + timedelta(1), scope_fingerprint="9" * 64)
        assert SOURCE.connector_problems(store, NOW) == {}
        assert SOURCE.connector_problems(store, NOW + timedelta(2)) == {
            "github": ["check_scope_changed:github"]
        }

    def test_other_connectors_are_independent(self, store):
        record(store, scope_fingerprint="9" * 64)
        record(store, connector="restic", expected_subjects={}, results=[])
        assert SOURCE.connector_problems(store, NOW) == {"github": ["check_scope_changed:github"]}


def test_last_run_failure_is_decided_by_the_matrix_not_the_source(store):
    record(store, complete=False, errors=["x"])
    assert SOURCE.connector_problems(store, NOW) == {}


class TestConfig:
    def test_unchanged_config_does_not_block(self, store):
        record(store)
        assert SOURCE.connector_problems(store, NOW) == {}

    def test_edited_config_blocks_until_check_runs_again(self, store):
        record(store)
        store.write_text(config_key(), "ejemplo: 2\n")
        assert SOURCE.connector_problems(store, NOW) == {"github": ["check_config_changed:github"]}
        record(store, at=NOW + timedelta(1))
        assert SOURCE.connector_problems(store, NOW + timedelta(1)) == {}

    def test_missing_config_is_visible(self, store, tmp_path):
        record(store)
        (tmp_path / "ws" / config_key()).unlink()
        assert SOURCE.connector_problems(store, NOW) == {"github": ["check_config_missing:github"]}

    def test_unreadable_config_is_visible(self, store):
        record(store)
        store.write_bytes(config_key(), b"\xff\xfe")
        assert SOURCE.connector_problems(store, NOW) == {"github": ["check_config_changed:github"]}

    def test_other_connectors_are_not_affected(self, store):
        record(store)
        record(store, connector="restic", expected_subjects={}, results=[])
        store.write_text(config_key("restic"), "ejemplo: 2\n")
        assert SOURCE.connector_problems(store, NOW) == {"restic": ["check_config_changed:restic"]}


class TestFutureRun:
    AHEAD = NOW + timedelta(days=2)
    FUTURE = "2026-11-01 12h00 UTC"

    def test_run_after_the_reference_clock_blocks_also_with_an_earlier_at(self, store):
        record(store)
        record(store, at=self.AHEAD, results=[result(CheckStatus.FAIL)])
        source = StoredCheckResults(reference_now=NOW + timedelta(hours=1))
        expected = {"github": [f"check_run_in_future:github:{self.FUTURE}"]}
        assert source.connector_problems(store, NOW) == expected
        assert source.connector_problems(store, self.AHEAD) == expected

    def test_run_not_after_the_reference_clock_does_not_block(self, store):
        record(store)
        source = StoredCheckResults(reference_now=NOW)
        assert source.connector_problems(store, NOW) == {}

    def test_a_clock_correction_stops_the_superseded_run_from_counting(self, store):
        record(store)
        record(store, at=self.AHEAD, results=[result(CheckStatus.FAIL)])
        corrected = NOW + timedelta(hours=1)
        fresh = stored(at=corrected, config_sha256=sha256_hex(store.read_bytes(config_key())))
        fresh = fresh.model_copy(update={"scope_fingerprint": scope_status(store).fingerprint})
        record_run(store, fresh, corrected, accept_clock_correction=True)
        source = StoredCheckResults(reference_now=corrected)

        assert source.connector_problems(store, self.AHEAD) == {}
        assert [r.finished_at for r in source.runs(store, self.AHEAD)] == [NOW, corrected]

    def test_newer_evidence_does_not_add_a_missing_run_to_a_future_run(self, store):
        store.write_text(config_key(), "asset_id: gh-taller\norganization: taller-norte\n")
        record(store, at=self.AHEAD)
        collect(store, collected_at=self.AHEAD + timedelta(hours=1), now=NOW)
        source = StoredCheckResults(reference_now=NOW)

        assert source.connector_problems(store, self.AHEAD + timedelta(hours=2)) == {
            "github": [f"check_run_in_future:github:{self.FUTURE}"]
        }


class TestUnknownConnector:
    def test_a_valid_record_in_an_unknown_directory_is_reported_without_raising(self, store):
        record(store, connector="foo")

        assert SOURCE.connector_problems(store, NOW) == {"foo": ["check_connector_unknown:foo"]}

    def test_known_connectors_stay_usable(self, store):
        record(store, connector="foo")
        record(store)

        assert "github" not in SOURCE.connector_problems(store, NOW)


class TestEvidence:
    def test_unreadable_manifest_listing_blocks_instead_of_clearing_the_anchor(self, store):
        store.write_text(config_key(), "asset_id: gh-taller\norganization: taller-norte\n")
        record(store)
        store.write_text("evidence/manifests/EV-20260601-deadbeef.yaml", "id: [roto")

        assert SOURCE.connector_problems(store, NOW) == {"github": ["evidence_unreadable:github"]}

    @pytest.mark.parametrize(
        ("pattern", "replacement"),
        [
            (r"^asset_id: .*$", "asset_id: gh-otro"),
            (r"^collected_at: .*$", "collected_at: '2026-05-01T00:00:00+00:00'"),
            (r"^  name: github$", "  name: githubx"),
        ],
    )
    def test_a_retouched_manifest_blocks_the_connector(self, store, pattern, replacement):
        store.write_text(config_key(), "asset_id: gh-taller\norganization: taller-norte\n")
        record(store)
        manifest = collect(store, collected_at=NOW, now=NOW)
        rewrite_manifest(store, manifest.id, pattern, replacement)

        assert SOURCE.connector_problems(store, NOW) == {
            "github": ["check_evidence_inconsistent:github"]
        }


class TestPrecedence:
    """Cuando concurren varios motivos solo se muestra el primero: alcance, configuración,
    evidencia. Un reloj adelantado se suma a cualquiera."""

    def unreadable_manifest(self, store):
        store.write_text("evidence/manifests/EV-20260601-deadbeef.yaml", "id: [roto")

    def test_scope_wins_over_config_and_evidence(self, store):
        record(store)
        edit(store, "system", "  - bk-externo\n", "")
        store.write_text(config_key(), "ejemplo: 2\n")
        self.unreadable_manifest(store)
        assert SOURCE.connector_problems(store, NOW) == {"github": ["check_scope_changed:github"]}

    def test_config_wins_over_evidence(self, store):
        record(store)
        store.write_text(config_key(), "ejemplo: 2\n")
        self.unreadable_manifest(store)
        assert SOURCE.connector_problems(store, NOW) == {"github": ["check_config_changed:github"]}

    def test_a_future_run_adds_to_the_other_reason(self, store):
        record(store, at=NOW + timedelta(days=2), scope_fingerprint="9" * 64)
        source = StoredCheckResults(reference_now=NOW)
        assert source.connector_problems(store, NOW + timedelta(days=3)) == {
            "github": [
                "check_run_in_future:github:2026-11-01 12h00 UTC",
                "check_scope_changed:github",
            ]
        }
