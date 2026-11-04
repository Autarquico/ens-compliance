"""Uso de la CLI de la pre-auditoría. Los escenarios de la spec están en `test_acceptance.py`."""

import pytest
from assessment.dossier.stages import AFTER, basic

from ens_evidence.preaudit.service import PREAUDITS_PREFIX

from .helpers import world_at

P = ["preaudit"]


@pytest.fixture(scope="module")
def stages():
    return basic()


@pytest.fixture
def world(stages, tmp_path, monkeypatch):
    world = world_at(stages, tmp_path, monkeypatch)
    world.at(AFTER)
    return world


# --- uso ---------------------------------------------------------------------------------------


def test_prepare_without_settings_is_a_configuration_error(world, stages):
    prepared = world.run(*P, "prepare", stages.dossier)

    assert prepared.exit_code == 2
    assert "configura `preaudit`" in prepared.output


def test_a_malformed_consent_is_a_usage_error(world, stages):
    ran = world.run(*P, "run", stages.dossier, "--consent", "abc", "--by", "Ana")

    assert ran.exit_code == 2 and "sha256" in ran.output
    assert world.store.list(PREAUDITS_PREFIX) == []
