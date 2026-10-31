import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from pydantic import BaseModel, ConfigDict

from ens_evidence.checks.inputs import (
    CHECK_EVIDENCE_INCONSISTENT,
    CHECK_EVIDENCE_UNVERIFIED,
    EVIDENCE_INCONSISTENT,
    EVIDENCE_UNREADABLE,
    EVIDENCE_UNVERIFIED,
    NO_EVIDENCE,
    evidence_after_run,
    load_endpoint_inputs,
    load_engine_config,
    select_collector_input,
)
from ens_evidence.checks.model import CheckInput, StoredCheckRun
from ens_evidence.connectors.github.config import GitHubConfig
from ens_evidence.endpoint.normalize import SOURCE as ENDPOINT_SOURCE
from ens_evidence.evidence.importer import (
    canonical_json,
    import_attested_package,
    import_collected,
    import_manual,
)
from ens_evidence.evidence.layout import artifact_key, manifest_key, sha256_hex
from ens_evidence.evidence.manifests import render_manifest
from ens_evidence.matrix.evidence import manifest_sha256
from ens_evidence.models.contracts import CollectionResult
from ens_evidence.models.enums import CollectionStatus
from ens_evidence.workspace import LocalStore

NOW = datetime(2026, 6, 1, 12, tzinfo=UTC)
ASSET = "gh-taller"
FIXTURES = Path(__file__).parents[1] / "connectors" / "github" / "fixtures"
ORG_SECTION = json.loads((FIXTURES / "org.json").read_text())


@pytest.fixture
def store(tmp_path) -> LocalStore:
    return LocalStore(tmp_path / "ws")


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_id: str


def result(
    *,
    collected_at: datetime = NOW,
    asset_id: str = ASSET,
    source: str = "github",
    collector: str = "github",
    marker: str = "a",
) -> CollectionResult:
    return CollectionResult(
        source=source,
        collector=collector,
        collected_at=collected_at,
        asset_id=asset_id,
        data={"sections": {"org": ORG_SECTION}, "marker": marker},
        collection_status=CollectionStatus.SUCCESS,
    )


def collect(store, *, now: datetime = NOW, **kwargs):
    return import_collected(store, result(**kwargs), collector_version="1.0", now=now)


def rewrite_manifest(store, evidence_id: str, pattern: str, replacement: str) -> None:
    key = manifest_key(evidence_id)
    text, count = re.subn(pattern, replacement, store.read_text(key), flags=re.M)
    assert count == 1
    store.write_text(key, text)


def select(store, connector: str = "github"):
    return select_collector_input(store, connector, ASSET)


def test_selects_latest_collected_at_with_manifest_sha(store):
    collect(store, collected_at=NOW - timedelta(days=1), marker="old")
    latest = collect(store, marker="new")

    selected = select(store)

    assert selected.ok
    assert selected.evidence_id == latest.id
    assert selected.result.data["marker"] == "new"
    assert selected.manifest_sha256 == manifest_sha256(latest)


def test_no_evidence_is_an_error_not_a_pass(store):
    assert select(store).error == NO_EVIDENCE


def test_older_valid_evidence_is_not_used_when_a_later_manifest_is_unreadable(store):
    collect(store, collected_at=NOW - timedelta(days=1))
    store.write_text("evidence/manifests/EV-20260601-deadbeef.yaml", "id: [roto")

    selected = select(store)

    assert selected.error == EVIDENCE_UNREADABLE
    assert selected.result is None
    assert selected.problems


def test_older_valid_evidence_is_not_used_when_the_latest_artifact_is_unreadable(store):
    collect(store, collected_at=NOW - timedelta(days=1), marker="old")
    latest = collect(store, marker="new")
    store.write_bytes(latest.artifact.key, b"{}")

    selected = select(store)

    assert selected.error == EVIDENCE_UNVERIFIED
    assert selected.evidence_id == latest.id
    assert selected.result is None


def test_tie_on_collected_at_is_broken_by_submitted_at(store):
    collect(store, now=NOW + timedelta(minutes=5), marker="later")
    collect(store, now=NOW, marker="earlier")

    assert select(store).result.data["marker"] == "later"


def test_full_tie_is_broken_by_the_highest_id(store):
    first = collect(store, marker="same")
    for suffix in ("aaaaaaaa", "ffffffff"):
        twin = first.model_copy(update={"id": f"EV-20260601-{suffix}"})
        store.write_bytes(manifest_key(twin.id), render_manifest(twin))

    assert select(store).evidence_id == "EV-20260601-ffffffff"


def test_ignores_other_asset_and_other_collector(store):
    mine = collect(store, collected_at=NOW - timedelta(days=1))
    collect(store, asset_id="gh-otro")
    collect(store, source="cloudflare", collector="cloudflare")

    assert select(store).evidence_id == mine.id


def test_other_collector_only_means_no_evidence(store):
    collect(store, source="cloudflare", collector="cloudflare")

    assert select(store).error == NO_EVIDENCE


def test_manifest_retouched_collected_at_is_inconsistent(store):
    manifest = collect(store)
    rewrite_manifest(
        store,
        manifest.id,
        r"^collected_at: .*$",
        f"collected_at: '{NOW + timedelta(days=1):%Y-%m-%dT%H:%M:%S}+00:00'",
    )

    selected = select(store)

    assert selected.error == EVIDENCE_INCONSISTENT
    assert any("collected_at" in problem for problem in selected.problems)


def test_manifest_retouched_asset_id_is_inconsistent(store):
    manifest = collect(store, asset_id="gh-ajeno")
    rewrite_manifest(store, manifest.id, r"^asset_id: .*$", f"asset_id: {ASSET}")

    selected = select(store)

    assert selected.error == EVIDENCE_INCONSISTENT
    assert any("asset_id" in problem for problem in selected.problems)


def test_manifest_retouched_source_is_inconsistent(store):
    manifest = collect(store)
    rewrite_manifest(store, manifest.id, r"^source: .*$", "source: gitlab")

    assert any("source" in problem for problem in select(store).problems)


def test_artifact_that_is_not_a_collection_result_is_inconsistent(store):
    manifest = collect(store)
    bogus = b'{"no":"es un resultado"}'
    digest = sha256_hex(bogus)
    store.write_bytes(artifact_key(digest), bogus)
    forged = manifest.model_copy(
        update={
            "artifact": manifest.artifact.model_copy(
                update={"key": artifact_key(digest), "sha256": digest}
            )
        }
    )
    store.write_bytes(manifest_key(manifest.id), render_manifest(forged))

    assert select(store).error == EVIDENCE_INCONSISTENT


def test_artifact_with_a_different_hash_is_unverified(store):
    manifest = collect(store)
    store.write_bytes(manifest.artifact.key, canonical_json(result(marker="otro")))

    selected = select(store)

    assert selected.error == EVIDENCE_UNVERIFIED
    assert selected.evidence_id == manifest.id


# --- endpoint ---------------------------------------------------------------------------------


def endpoint_result(asset_id: str = "lap-ana") -> CollectionResult:
    return result(asset_id=asset_id, source=ENDPOINT_SOURCE, collector=ENDPOINT_SOURCE)


def import_package(store, res: CollectionResult | None = None):
    return import_attested_package(
        store,
        b"paquete-sintetico",
        res or endpoint_result(),
        package_source="endpoint-package",
        collector_version="1.0",
        submitted_by="ana",
        valid_until=NOW + timedelta(days=30),
        now=NOW,
    )


def test_manual_endpoint_package_is_valid_without_collector_provenance(store):
    _, normalized = import_package(store)

    loaded = load_endpoint_inputs(store)

    assert [item.evidence.evidence_id for item in loaded.valid] == [normalized.id]
    assert loaded.valid[0].manifest_sha256 == manifest_sha256(normalized)
    assert loaded.inconsistent == []
    assert loaded.unreadable == []


def test_endpoint_with_mismatching_identity_is_flagged_not_valid(store):
    _, normalized = import_package(store)
    rewrite_manifest(store, normalized.id, r"^asset_id: .*$", "asset_id: lap-otro")

    loaded = load_endpoint_inputs(store)

    assert loaded.valid == []
    assert len(loaded.inconsistent) == 1
    assert normalized.id in loaded.inconsistent[0]
    assert "asset_id" in loaded.inconsistent[0]


def test_endpoint_with_mismatching_collected_at_is_flagged(store):
    _, normalized = import_package(store)
    rewrite_manifest(
        store, normalized.id, r"^collected_at: .*$", "collected_at: '2026-05-01T00:00:00+00:00'"
    )

    assert "collected_at" in load_endpoint_inputs(store).inconsistent[0]


def test_endpoint_with_unreadable_manifest_is_reported(store):
    import_package(store)
    store.write_text("evidence/manifests/EV-20260601-deadbeef.yaml", "id: [roto")

    assert load_endpoint_inputs(store).unreadable


# --- evidencia posterior a la última ejecución ---------------------------------------------------


def run_citing(*evidence_ids: str, finished_at: datetime = NOW) -> StoredCheckRun:
    return StoredCheckRun(
        seq=1,
        connector="github",
        engine_version="1",
        started_at=finished_at,
        finished_at=finished_at,
        complete=True,
        scope_fingerprint="a" * 64,
        config_sha256="b" * 64,
        inputs=[CheckInput(evidence_id=i, manifest_sha256="c" * 64) for i in evidence_ids],
    )


def after_run(store, run, at=NOW + timedelta(days=1)) -> str | None:
    return evidence_after_run(store, "github", run, at, ASSET)


def test_the_evidence_the_run_cited_is_not_newer(store):
    manifest = collect(store)

    assert not after_run(store, run_citing(manifest.id))


def test_newer_evidence_that_the_run_did_not_cite_is_flagged(store):
    old = collect(store, collected_at=NOW - timedelta(days=1))
    collect(store, collected_at=NOW)

    assert after_run(store, run_citing(old.id)) == "check_run_missing"


def test_evidence_collected_after_the_query_instant_is_ignored(store):
    old = collect(store, collected_at=NOW - timedelta(days=1))
    collect(store, collected_at=NOW)

    assert not after_run(store, run_citing(old.id), at=NOW - timedelta(hours=1))


def test_no_evidence_means_nothing_is_missing(store):
    assert not after_run(store, run_citing())


def test_unverifiable_artifact_blocks_instead_of_falling_back_to_older_evidence(store):
    older = collect(store, collected_at=NOW - timedelta(days=1))
    latest = collect(store, collected_at=NOW)
    store.write_bytes(latest.artifact.key, b"{}")

    assert after_run(store, run_citing(older.id), at=NOW) == CHECK_EVIDENCE_UNVERIFIED


# --- SEC-5: la identidad sale del artefacto, no de los campos editables del manifiesto -----------

RETOUCHES = {
    "asset_id": (r"^asset_id: .*$", "asset_id: gh-otro"),
    "collected_at": (r"^collected_at: .*$", "collected_at: '2026-05-01T00:00:00+00:00'"),
    "collector": (r"^  name: github$", "  name: githubx"),
}


@pytest.mark.parametrize("field", RETOUCHES)
def test_retouching_the_latest_manifest_never_selects_the_older_evidence(store, field):
    collect(store, collected_at=NOW - timedelta(days=1), marker="old")
    latest = collect(store, marker="new")
    rewrite_manifest(store, latest.id, *RETOUCHES[field])

    selected = select(store)

    assert selected.error == EVIDENCE_INCONSISTENT
    assert selected.result is None
    assert selected.evidence_id == latest.id


@pytest.mark.parametrize("field", RETOUCHES)
def test_retouching_the_latest_manifest_blocks_the_status_too(store, field):
    old = collect(store, collected_at=NOW - timedelta(days=1), marker="old")
    latest = collect(store, marker="new")
    rewrite_manifest(store, latest.id, *RETOUCHES[field])

    assert after_run(store, run_citing(old.id)) == CHECK_EVIDENCE_INCONSISTENT


def test_a_retouched_manifest_of_another_asset_blocks_the_connector(store):
    collect(store)
    other = collect(store, asset_id="gh-otro")
    rewrite_manifest(store, other.id, r"^asset_id: .*$", "asset_id: gh-tercero")

    assert select(store).error == EVIDENCE_INCONSISTENT


def test_a_retouched_manifest_of_another_collector_does_not_block_this_one(store):
    mine = collect(store)
    other = collect(store, source="cloudflare", collector="cloudflare")
    rewrite_manifest(store, other.id, r"^asset_id: .*$", "asset_id: cf-otro")

    assert select(store).evidence_id == mine.id


def test_a_retouched_collector_name_blocks_the_real_connector_and_the_claimed_one(store):
    mine = collect(store)
    other = collect(store, source="cloudflare", collector="cloudflare")
    rewrite_manifest(store, other.id, r"^  name: cloudflare$", "  name: github")

    assert select(store).error == EVIDENCE_INCONSISTENT
    assert select(store, "cloudflare").error == EVIDENCE_INCONSISTENT
    assert mine.id


def test_legitimate_evidence_of_several_assets_and_collectors_still_selects(store):
    mine = collect(store, collected_at=NOW - timedelta(hours=1))
    collect(store, asset_id="gh-otro")
    collect(store, source="cloudflare", collector="cloudflare")

    assert select(store).evidence_id == mine.id
    assert after_run(store, run_citing(mine.id)) is None


def test_an_artifact_that_is_not_a_result_blocks_every_connector(store):
    manifest = collect(store, source="cloudflare", collector="cloudflare")
    bogus = b'{"no":"es un resultado"}'
    digest = sha256_hex(bogus)
    store.write_bytes(artifact_key(digest), bogus)
    forged = manifest.model_copy(
        update={
            "artifact": manifest.artifact.model_copy(
                update={"key": artifact_key(digest), "sha256": digest}
            )
        }
    )
    store.write_bytes(manifest_key(manifest.id), render_manifest(forged))

    assert select(store).error == EVIDENCE_INCONSISTENT


def test_endpoint_package_with_a_retouched_source_is_flagged_not_skipped(store):
    _, normalized = import_package(store)
    rewrite_manifest(store, normalized.id, r"^source: .*$", "source: otro")

    loaded = load_endpoint_inputs(store)

    assert loaded.valid == []
    assert any(normalized.id in item and "source" in item for item in loaded.inconsistent)


def test_endpoint_inconsistency_blocks_the_status(store):
    _, normalized = import_package(store)
    rewrite_manifest(store, normalized.id, r"^asset_id: .*$", "asset_id: lap-otro")

    assert endpoint_after_run(store, run_citing(normalized.id)) == CHECK_EVIDENCE_INCONSISTENT


def test_an_unreadable_manifest_blocks_instead_of_saying_nothing_is_missing(store):
    manifest = collect(store)
    store.write_text("evidence/manifests/EV-20260601-deadbeef.yaml", "id: [roto")

    assert after_run(store, run_citing(manifest.id)) == "evidence_unreadable"


def endpoint_after_run(store, run) -> str | None:
    return evidence_after_run(store, "endpoint", run, NOW + timedelta(days=1))


def test_endpoint_evidence_not_cited_by_the_run_is_flagged(store):
    _, normalized = import_package(store)

    assert endpoint_after_run(store, run_citing()) == "check_run_missing"
    assert not endpoint_after_run(store, run_citing(normalized.id))


def test_endpoint_with_an_unreadable_manifest_blocks(store):
    _, normalized = import_package(store)
    store.write_text("evidence/manifests/EV-20260601-deadbeef.yaml", "id: [roto")

    assert endpoint_after_run(store, run_citing(normalized.id)) == "evidence_unreadable"


# --- configuración ------------------------------------------------------------------------------


def test_config_returns_model_and_file_sha(store):
    body = b"asset_id: gh-taller\norganization: taller-norte\n"
    store.write_bytes("connectors/github.yaml", body)

    loaded = load_engine_config(store, "github", GitHubConfig)

    assert loaded.error is None
    assert loaded.config.asset_id == ASSET
    assert loaded.sha256 == sha256_hex(body)


def test_missing_config_is_an_error(store):
    loaded = load_engine_config(store, "github", GitHubConfig)

    assert loaded.config is None
    assert loaded.sha256 is None
    assert "connectors/github.yaml" in loaded.error


def test_invalid_config_is_an_error_without_values(store):
    store.write_text(
        "connectors/github.yaml", yaml.safe_dump({"asset_id": "", "organization": "-x"})
    )

    loaded = load_engine_config(store, "github", GitHubConfig)

    assert loaded.config is None
    assert loaded.error


# --- procedencia declarada -----------------------------------------------------------------------


def test_a_retouched_provenance_kind_never_selects_the_older_evidence(store):
    collect(store, collected_at=NOW - timedelta(days=1), marker="old")
    latest = collect(store, marker="new")
    rewrite_manifest(store, latest.id, r"^  kind: collector$", "  kind: manual")

    selected = select(store)

    assert selected.error == EVIDENCE_INCONSISTENT
    assert selected.result is None
    assert selected.evidence_id == latest.id


def test_a_retouched_provenance_kind_blocks_the_status_too(store):
    old = collect(store, collected_at=NOW - timedelta(days=1), marker="old")
    latest = collect(store, marker="new")
    rewrite_manifest(store, latest.id, r"^  kind: collector$", "  kind: manual")

    assert after_run(store, run_citing(old.id)) == CHECK_EVIDENCE_INCONSISTENT


def test_a_manual_evidence_does_not_affect_the_connector(store):
    mine = collect(store)
    import_manual(
        store, b"politica en pdf", asset_id=ASSET, submitted_by="ana", now=NOW + timedelta(hours=1)
    )
    import_package(store)

    assert select(store).evidence_id == mine.id
    assert after_run(store, run_citing(mine.id)) is None
