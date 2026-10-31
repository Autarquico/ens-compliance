import json
from datetime import UTC, datetime, timedelta

import pytest

from ens_evidence.checks.model import CheckInput, StoredCheckRun
from ens_evidence.checks.store import (
    CheckHistoryUnreadable,
    RecordOutcome,
    canonical_bytes,
    outcome_digest,
    record_run,
    run_key,
    scan_runs,
)
from ens_evidence.evidence.layout import CHECKS_PREFIX
from ens_evidence.models.contracts import CheckResult
from ens_evidence.models.enums import CheckStatus
from ens_evidence.workspace import LocalStore

NOW = datetime(2026, 10, 30, 12, tzinfo=UTC)
SCOPE = "a" * 64
CONFIG = "b" * 64
MANIFEST = "c" * 64
CHECK = "github.default_branch_protected"


@pytest.fixture
def store(tmp_path):
    return LocalStore(tmp_path / "ws")


def result(status=CheckStatus.PASS, *, reason="r", subject="repo-a") -> CheckResult:
    return CheckResult(
        check_id=CHECK,
        asset_id=subject,
        status=status,
        evaluated_at=NOW,
        evidence_ids=["EV-20261029-aaaaaaaa"],
        reason=reason,
    )


def stored(*, connector="github", at=NOW, **changes) -> StoredCheckRun:
    fields = {
        "seq": 1,
        "connector": connector,
        "engine_version": "1",
        "started_at": at - timedelta(minutes=1),
        "finished_at": at,
        "complete": True,
        "scope_fingerprint": SCOPE,
        "config_sha256": CONFIG,
        "inputs": [CheckInput(evidence_id="EV-20261029-aaaaaaaa", manifest_sha256=MANIFEST)],
        "expected_subjects": {CHECK: ["repo-a"]},
        "results": [result()],
        "errors": [],
    }
    return StoredCheckRun(**{**fields, **changes})


def record(store, run=None) -> RecordOutcome:
    run = run or stored()
    return record_run(store, run, run.finished_at + timedelta(minutes=1))


def keys(store, connector="github"):
    return store.list(f"{CHECKS_PREFIX}/{connector}")


class TestKey:
    def test_has_connector_seq_instant_and_hash_of_the_canonical_bytes(self, store):
        outcome = record(store)
        data = store.read_bytes(outcome.key)
        assert outcome.created
        assert outcome.key == run_key(stored(), data)
        assert outcome.key.startswith(f"{CHECKS_PREFIX}/github/000001-20261030T120000000000Z-")
        assert data == canonical_bytes(stored())
        assert json.loads(data)["seq"] == 1

    def test_seq_is_assigned_by_the_store_and_increases(self, store):
        record(store, stored(seq=7))
        second = record(store, stored(results=[result(CheckStatus.FAIL)], at=NOW + timedelta(1)))
        assert second.key.split("/")[-1].startswith("000002-")

    def test_connectors_have_independent_sequences(self, store):
        record(store)
        other = record(store, stored(connector="restic", results=[], expected_subjects={}))
        assert other.key.startswith(f"{CHECKS_PREFIX}/restic/000001-")

    def test_a_run_finished_in_the_future_is_rejected(self, store):
        with pytest.raises(ValueError, match="posterior"):
            record_run(store, stored(), NOW - timedelta(seconds=1))
        assert keys(store) == []


class TestScan:
    def test_reads_what_was_recorded_in_order(self, store):
        record(store)
        record(store, stored(results=[result(CheckStatus.FAIL)], at=NOW + timedelta(1)))
        scan = scan_runs(store)
        assert [s.run.seq for s in scan.runs["github"]] == [1, 2]
        assert scan.unreadable == {}

    def test_no_history_is_not_a_problem(self, store):
        scan = scan_runs(store)
        assert (scan.runs, scan.unreadable) == ({}, {})

    def test_edited_content_is_unreadable(self, store):
        key = record(store).key
        store.write_bytes(key, store.read_bytes(key).replace(b'"FAIL"', b'"PASS"') + b" ")
        assert scan_runs(store).unreadable == {"github": (key,)}

    def test_a_status_flipped_in_place_is_unreadable(self, store):
        key = record(store, stored(results=[result(CheckStatus.FAIL)])).key
        store.write_bytes(key, store.read_bytes(key).replace(b'"FAIL"', b'"PASS"'))
        assert scan_runs(store).unreadable == {"github": (key,)}

    def test_renamed_file_is_unreadable(self, store):
        key = record(store).key
        renamed = key.replace("000001-", "000002-")
        store.write_bytes(renamed, store.read_bytes(key))
        store._root.joinpath(key).unlink()
        assert scan_runs(store).unreadable == {"github": (renamed,)}

    def test_a_different_instant_in_the_name_is_unreadable(self, store):
        key = record(store).key
        moved = key.replace("20261030T120000", "20261029T120000")
        store.write_bytes(moved, store.read_bytes(key))
        store._root.joinpath(key).unlink()
        assert scan_runs(store).unreadable == {"github": (moved,)}

    def test_duplicated_seq_marks_both_files(self, store):
        first = record(store).key
        other = stored(results=[result(CheckStatus.FAIL)], at=NOW + timedelta(1))
        data = canonical_bytes(other)
        second = run_key(other, data)
        store.write_bytes(second, data)
        assert set(scan_runs(store).unreadable["github"]) == {first, second}

    def test_gap_in_the_sequence_is_unreadable(self, store):
        record(store)
        middle = record(store, stored(results=[result(CheckStatus.FAIL)], at=NOW + timedelta(1)))
        record(store, stored(at=NOW + timedelta(2), config_sha256="d" * 64))
        store._root.joinpath(middle.key).unlink()
        assert scan_runs(store).unreadable == {"github": (f"{CHECKS_PREFIX}/github/seq",)}

    def test_file_copied_into_another_connector_directory_is_unreadable(self, store):
        key = record(store).key
        foreign = key.replace("/github/", "/restic/")
        store.write_bytes(foreign, store.read_bytes(key))
        scan = scan_runs(store)
        assert scan.unreadable == {"restic": (foreign,)}
        assert "github" in scan.runs

    @pytest.mark.parametrize(
        "content",
        [b"no es json", b'{"seq":1,"seq":1}', b"\xff\xfe", b"[]"],
        ids=["garbage", "repeated-key", "not-utf8", "not-an-object"],
    )
    def test_unparseable_content_is_unreadable(self, store, content):
        key = f"{CHECKS_PREFIX}/github/000001-20261030T120000000000Z-{'0' * 12}.json"
        store.write_bytes(key, content)
        assert scan_runs(store).unreadable == {"github": (key,)}

    def test_stray_file_at_the_root_is_not_attributable(self, store):
        store.write_bytes(f"{CHECKS_PREFIX}/stray.json", b"{}")
        assert scan_runs(store).unreadable == {"": (f"{CHECKS_PREFIX}/stray.json",)}

    def test_scanning_one_connector_ignores_the_others(self, store):
        record(store)
        store.write_bytes(f"{CHECKS_PREFIX}/restic/junk.json", b"{}")
        scan = scan_runs(store, "github")
        assert scan.unreadable == {} and list(scan.runs) == ["github"]

    def test_known_limit_removing_the_latest_runs_leaves_no_trace(self, store):
        """Límite conocido (ADR 0012): quitar los últimos registros no es detectable dentro del
        historial. Se contrasta con la copia externa (`backup` + `evidence verify`)."""
        record(store, stored(results=[result(CheckStatus.PASS)]))
        failing = record(
            store, stored(results=[result(CheckStatus.FAIL)], at=NOW + timedelta(1))
        ).key
        store._root.joinpath(failing).unlink()
        scan = scan_runs(store)
        assert scan.unreadable == {}
        assert scan.last("github").run.results[0].status is CheckStatus.PASS


class TestDeduplication:
    def test_same_outcome_again_writes_nothing(self, store):
        record(store)
        again = record(store, stored(at=NOW + timedelta(hours=1)))
        assert again == RecordOutcome(None, False)
        assert len(keys(store)) == 1

    def test_instants_and_reasons_do_not_count(self):
        later = stored(
            at=NOW + timedelta(days=1),
            results=[result(reason="otro texto").model_copy(update={"evaluated_at": NOW})],
        )
        assert outcome_digest(later) == outcome_digest(stored())

    def test_result_order_does_not_count(self):
        a, b = result(subject="repo-a"), result(subject="repo-b")
        assert outcome_digest(stored(results=[a, b])) == outcome_digest(stored(results=[b, a]))

    def test_new_evidence_with_the_same_result_is_a_new_run(self, store):
        record(store)
        inputs = [CheckInput(evidence_id="EV-20261029-aaaaaaaa", manifest_sha256="e" * 64)]
        assert record(store, stored(inputs=inputs, at=NOW + timedelta(1))).created

    def test_new_evidence_id_is_a_new_run(self, store):
        record(store)
        inputs = [CheckInput(evidence_id="EV-20261030-bbbbbbbb", manifest_sha256=MANIFEST)]
        assert record(store, stored(inputs=inputs, at=NOW + timedelta(1))).created

    def test_config_change_is_a_new_run(self, store):
        record(store)
        assert record(store, stored(config_sha256="d" * 64, at=NOW + timedelta(1))).created

    def test_scope_change_is_a_new_run(self, store):
        record(store)
        assert record(store, stored(scope_fingerprint="d" * 64, at=NOW + timedelta(1))).created

    def test_engine_version_change_is_a_new_run(self, store):
        record(store)
        assert record(store, stored(engine_version="2", at=NOW + timedelta(1))).created

    def test_expected_subjects_change_is_a_new_run(self, store):
        record(store)
        expected = {CHECK: ["repo-a", "repo-b"]}
        assert record(store, stored(expected_subjects=expected, at=NOW + timedelta(1))).created

    def test_status_change_is_a_new_run(self, store):
        record(store)
        failing = stored(results=[result(CheckStatus.FAIL)], at=NOW + timedelta(1))
        assert record(store, failing).created

    def test_error_change_is_a_new_run(self, store):
        failing = stored(complete=False, errors=["fallo uno"])
        record(store, failing)
        assert not record(store, stored(complete=False, errors=["fallo uno"])).created
        other = stored(complete=False, errors=["fallo dos"], at=NOW + timedelta(1))
        assert record(store, other).created

    def test_complete_flag_is_part_of_the_outcome(self, store):
        record(store, stored(complete=False))
        assert record(store, stored(at=NOW + timedelta(1))).created

    def test_only_the_last_attempt_is_compared_so_a_b_a_leaves_three_runs(self, store):
        passing = stored(results=[result(CheckStatus.PASS)])
        failing = stored(results=[result(CheckStatus.FAIL)])
        record(store, passing)
        record(store, failing.model_copy(update={"finished_at": NOW + timedelta(1)}))
        third = record(store, passing.model_copy(update={"finished_at": NOW + timedelta(2)}))
        assert third.created and len(keys(store)) == 3

    def test_existing_key_is_not_overwritten(self, store):
        key = record(store).key
        before = store.read_bytes(key)
        # Escritura concurrente del mismo contenido: la clave ya existe y no se pisa.
        store.create_bytes(
            run_key(stored(seq=2), canonical_bytes(stored(seq=2))), canonical_bytes(stored(seq=2))
        )
        assert store.read_bytes(key) == before


class TestUnreadableHistoryIsNotExtended:
    def test_raises_and_writes_nothing(self, store):
        key = record(store).key
        store.write_bytes(key, b"{}")
        with pytest.raises(CheckHistoryUnreadable) as caught:
            record(store, stored(results=[result(CheckStatus.FAIL)], at=NOW + timedelta(1)))
        assert caught.value.keys == (key,)
        assert len(keys(store)) == 1

    def test_other_connectors_are_not_affected(self, store):
        store.write_bytes(f"{CHECKS_PREFIX}/restic/junk.json", b"{}")
        assert record(store).created
