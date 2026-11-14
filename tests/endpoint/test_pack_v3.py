import json
from datetime import timedelta

import pytest

from ens_evidence.endpoint.evaluate import evaluate_workspace
from ens_evidence.endpoint.evidence import load_host_evidence
from ens_evidence.endpoint.normalize import STORED_COLUMNS
from ens_evidence.endpoint.pack import PACK_DIR, load_pack
from ens_evidence.evidence import list_manifests
from ens_evidence.evidence.layout import artifact_key, manifest_key, sha256_hex

from .support import import_package, members_of, normalized, persisted_keys, rebuilt
from .test_hostile_import import rejected

WINDOWS_QUERIES = ("bitlocker_info", "windows_security_center", "programs")


def test_the_pack_is_version_3_and_has_the_windows_queries():
    pack = load_pack()

    assert pack.version == 3
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


def test_stored_v1_script_evidence_still_evaluates(store, laptop_package):
    import_package(store, laptop_package)
    manifest = next(m for m in list_manifests(store).manifests if m.source == "endpoint-script")
    stored = normalized(store)
    stored["data"]["package"]["pack_version"] = "1"
    artifact = json.dumps(stored).encode()
    old_key, new_key = manifest.artifact.key, artifact_key(sha256_hex(artifact))
    store.write_bytes(new_key, artifact)
    path = manifest_key(manifest.id)
    store.write_text(
        path,
        store.read_text(path)
        .replace(old_key, new_key)
        .replace(manifest.artifact.sha256, sha256_hex(artifact)),
    )
    loaded = load_host_evidence(store)
    assert loaded.unreadable == []
    assert loaded.items[0].result.data["package"]["pack_version"] == "1"

    results = evaluate_workspace(store, loaded.items[0].collected_at + timedelta(minutes=1)).results

    assert (
        next(r for r in results if r.check_id == "HOST-003" and r.asset_id == "lap-ana").status.name
        == "PASS"
    )


def test_a_pending_v2_package_is_rejected_asking_to_repeat_the_collection(store, laptop_package):
    message = rejected(store, rebuilt(laptop_package, meta={"pack_version": "2"}))

    assert "versión 2" in message
    assert "repite la recogida" in message


def test_the_antimalware_queries_belong_to_their_platforms():
    platforms = {q.id: q.platforms for q in load_pack().queries}

    assert platforms["windows_security_products"] == ("windows",)
    assert platforms["gatekeeper"] == platforms["xprotect_bundle"] == ("darwin",)
    assert platforms["clamav_signatures"] == platforms["clamav_units"] == ("linux",)
