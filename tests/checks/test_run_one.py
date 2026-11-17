"""`run_one` evalúa un solo conector con el mismo resultado que `run_all`."""

import pytest

from ens_evidence.checks.engine import OutcomeKind, run_all, run_one
from ens_evidence.scope import ScopeNotApproved
from ens_evidence.scope.approval import APPROVAL_KEY

from .test_engine import NOW, collect


def test_run_one_matches_the_github_outcome_of_run_all(store):
    collect(store)
    expected = next(o for o in run_all(store, NOW) if o.connector == "github")

    outcome = run_one(store, "github", NOW)

    assert expected.kind is OutcomeKind.RECORDED
    assert (outcome.kind, outcome.run) == (OutcomeKind.UNCHANGED, expected.run)


def test_run_one_has_nothing_to_say_about_a_connector_the_engine_does_not_evaluate(store):
    assert run_one(store, "no-existe", NOW) is None


def test_run_one_requires_an_approved_scope(store):
    (store._root / APPROVAL_KEY).unlink()

    with pytest.raises(ScopeNotApproved):
        run_one(store, "github", NOW)
