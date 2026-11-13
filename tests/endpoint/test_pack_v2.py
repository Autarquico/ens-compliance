import json

import pytest

from ens_evidence.endpoint.normalize import STORED_COLUMNS
from ens_evidence.endpoint.pack import PACK_DIR, load_pack

from .check_data import evaluate, host_evidence
from .support import members_of, persisted_keys, rebuilt
from .test_hostile_import import rejected

WINDOWS_QUERIES = ("bitlocker_info", "windows_security_center", "programs")


def test_the_pack_is_version_2_and_has_the_windows_queries():
    pack = load_pack()

    assert pack.version == 2
    windows = {q.id for q in pack.queries if "windows" in q.platforms}
    assert windows >= {*WINDOWS_QUERIES, "system_info", "os_version", "osquery_info"}


@pytest.mark.parametrize("query", load_pack().queries, ids=lambda q: q.id)
def test_every_query_has_explicit_columns_that_are_all_stored(query):
    sql = (PACK_DIR / f"{query.id}.sql").read_text("utf-8")

    assert "*" not in sql
    assert query.verified is False
    assert query.id in STORED_COLUMNS


def test_the_script_keeps_listing_only_its_own_platforms(laptop_package):
    meta = json.loads(members_of(laptop_package)["meta.json"])

    assert not set(WINDOWS_QUERIES) & {run["id"] for run in meta["queries"]}


def test_a_pending_v1_package_is_rejected_asking_to_repeat_the_collection(store, laptop_package):
    message = rejected(store, rebuilt(laptop_package, meta={"pack_version": "1"}))

    assert "repite la recogida" in message
    assert persisted_keys(store) == []


def test_stored_v1_script_evidence_still_evaluates():
    evidence = host_evidence()
    evidence.result.data["package"]["pack_version"] = "1"

    results = evaluate(evidence)

    assert results["HOST-003"].status.name == "PASS"
