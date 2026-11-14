from datetime import UTC, datetime

import pytest
from connectors.github.check_data import branch_item, repo, section, two_factor
from scope.scope_helpers import EXAMPLE_SCOPE, SCOPE_FILES, edit

from ens_evidence.checks.catalog import CHECKS, KNOWN_CHECKS
from ens_evidence.checks.expected import (
    INVENTORY_ASSET_INVALID,
    ExpectedError,
    expected_subjects,
)
from ens_evidence.checks.registry import CHECK_FUNCTIONS
from ens_evidence.connectors.github.config import GitHubConfig
from ens_evidence.endpoint.checks import HOST_CHECKS
from ens_evidence.models.contracts import CollectionResult
from ens_evidence.scope import load_inventory
from ens_evidence.workspace import LocalStore

COLLECTED = datetime(2026, 10, 29, 9, tzinfo=UTC)
CONFIG = GitHubConfig(asset_id="gh-taller", organization="taller-norte")
PER_REPOSITORY = [c for c, s in CHECKS.items() if s.connector == "github" and s.skip_archived]
SKIP_ARCHIVED = ["github.default_branch_protected", "github.secret_scanning_enabled",
                 "github.dependabot_alerts_enabled"]  # fmt: skip


@pytest.fixture
def store(tmp_path):
    workspace = LocalStore(tmp_path / "ws")
    for name in SCOPE_FILES:
        workspace.write_text(
            f"scope/{name}.yaml", (EXAMPLE_SCOPE / f"{name}.yaml").read_text(encoding="utf-8")
        )
    return workspace


def inventory(store):
    loaded = load_inventory(store)
    assert loaded.inventory is not None, loaded.errors
    return loaded.inventory


def feature_items(names):
    return [{"repository": n, "status": "SUCCESS", "state": "enabled"} for n in names]


def gh_evidence(repos: dict[str, bool], *, missing=(), collected_at=COLLECTED) -> CollectionResult:
    """Recogida de GitHub sana; `repos`: nombre -> archivado."""
    return CollectionResult(
        source="github",
        collector="github",
        collected_at=collected_at,
        asset_id="gh-taller",
        data={
            "organization": "taller-norte",
            "error": None,
            "sections": {
                "organization": two_factor(True),
                "repositories": section(
                    [repo(n, archived=a) for n, a in repos.items()],
                    missing_from_token=list(missing),
                ),
                "secret_scanning": section(feature_items(repos)),
                "dependabot": section(feature_items(repos)),
                "branch_rules": section([branch_item(n) for n in repos]),
            },
        },
        collection_status="SUCCESS",
    )


def expected(store, evidence, config=CONFIG):
    return expected_subjects("github", config, inventory(store), evidence)


def test_github_expects_the_organization_for_2fa_and_inventory_repos_for_the_rest(store):
    found = expected(store, gh_evidence({"facturas": False, "infra": False}))

    assert found["github.org_2fa_required"] == ["taller-norte"]
    for check_id in PER_REPOSITORY:
        assert found[check_id] == ["facturas", "infra"]
    assert set(found) == {c for c, s in CHECKS.items() if s.connector == "github"}


def test_without_evidence_every_inventory_repo_is_expected(store):
    found = expected(store, None)

    assert found["github.secret_scanning_enabled"] == ["facturas", "infra"]


@pytest.mark.parametrize("check_id", SKIP_ARCHIVED)
def test_archived_repo_is_expected_only_while_it_is_not_archived(store, check_id):
    live = expected(store, gh_evidence({"facturas": False, "infra": False}))
    archived = expected(store, gh_evidence({"facturas": False, "infra": True}))
    restored = expected(store, gh_evidence({"facturas": False, "infra": False}))

    assert live[check_id] == ["facturas", "infra"]
    assert archived[check_id] == ["facturas"]
    assert restored == live


@pytest.mark.parametrize("check_id", SKIP_ARCHIVED)
def test_expected_subjects_match_what_the_check_produces(store, check_id):
    for repos in ({"facturas": False, "infra": False}, {"facturas": False, "infra": True}):
        evidence = gh_evidence(repos)
        produced = {r.subject for r in CHECK_FUNCTIONS[check_id](evidence, "EV-1", COLLECTED)}
        assert produced == set(expected(store, evidence)[check_id])


def test_org_2fa_is_expected_on_the_organization_as_the_check_produces_it(store):
    evidence = gh_evidence({"facturas": False, "infra": False})

    produced = CHECK_FUNCTIONS["github.org_2fa_required"](evidence, "EV-1", COLLECTED)

    assert {r.subject for r in produced} == set(
        expected(store, evidence)["github.org_2fa_required"]
    )


def test_repo_spelling_follows_the_evidence_not_the_inventory(store):
    edit(store, "assets", "repos: [facturas, infra]", "repos: [Facturas, INFRA]")
    evidence = gh_evidence({"facturas": False, "infra": False})

    found = expected(store, evidence)

    assert found["github.secret_scanning_enabled"] == ["facturas", "infra"]
    produced = CHECK_FUNCTIONS["github.secret_scanning_enabled"](evidence, "EV-1", COLLECTED)
    assert {r.subject for r in produced} == {"facturas", "infra"}


def test_archived_is_found_whatever_the_spelling_in_the_inventory(store):
    edit(store, "assets", "repos: [facturas, infra]", "repos: [Facturas, INFRA]")

    found = expected(store, gh_evidence({"facturas": False, "infra": True}))

    assert found["github.secret_scanning_enabled"] == ["facturas"]


def test_repo_invisible_to_the_token_keeps_the_inventory_spelling(store):
    edit(store, "assets", "repos: [facturas, infra]", "repos: [facturas, Infra]")
    evidence = gh_evidence({"facturas": False}, missing=["Infra"])

    found = expected(store, evidence)["github.default_branch_protected"]
    produced = CHECK_FUNCTIONS["github.default_branch_protected"](evidence, "EV-1", COLLECTED)

    assert found == ["Infra", "facturas"]
    assert {r.subject for r in produced} == set(found)


def test_evidence_with_repositories_not_collected_ignores_archived(store):
    evidence = gh_evidence({"facturas": False, "infra": True})
    evidence.data["sections"]["repositories"]["status"] = "ERROR"

    found = expected(store, evidence)

    assert found["github.secret_scanning_enabled"] == ["facturas", "infra"]


@pytest.mark.parametrize(
    "junk", [None, [], "x", {"items": "x"}, {"status": "SUCCESS", "items": [1, {}]}]
)
def test_malformed_repositories_section_does_not_raise(store, junk):
    evidence = gh_evidence({"facturas": False})
    evidence.data["sections"]["repositories"] = junk

    assert expected(store, evidence)["github.secret_scanning_enabled"] == ["facturas", "infra"]


def test_unknown_asset_cannot_be_expected(store):
    config = GitHubConfig(asset_id="no-existe", organization="taller-norte")

    with pytest.raises(ExpectedError) as raised:
        expected(store, None, config)

    assert raised.value.code == INVENTORY_ASSET_INVALID


def test_asset_of_another_type_cannot_be_expected(store):
    config = GitHubConfig(asset_id="srv-app", organization="taller-norte")

    with pytest.raises(ExpectedError):
        expected(store, None, config)


def test_inventory_repo_of_another_organization_cannot_be_expected(store):
    edit(store, "assets", "repos: [facturas, infra]", "repos: [otra-org/facturas, infra]")

    with pytest.raises(ExpectedError) as raised:
        expected(store, None)

    assert raised.value.code == INVENTORY_ASSET_INVALID


def test_endpoint_expects_every_laptop_and_server_of_the_inventory(store):
    found = expected_subjects("endpoint", object(), inventory(store), None)  # type: ignore[arg-type]

    assert set(found) == set(HOST_CHECKS)
    for check_id, hosts in found.items():
        if check_id == "HOST-009":  # protección en tiempo real: solo puestos
            assert hosts == ["lap-ana", "lap-personal"]
        else:
            assert hosts == ["lap-ana", "lap-personal", "srv-app"]


@pytest.mark.parametrize("connector", ["cloudflare", "google-workspace", "restic"])
def test_other_connectors_have_no_inventory_subjects(store, connector):
    found = expected_subjects(connector, object(), inventory(store), None)  # type: ignore[arg-type]

    assert set(found) == {c for c, v in KNOWN_CHECKS.items() if v == connector}
    assert all(subjects == [] for subjects in found.values())
