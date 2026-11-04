"""Los cuatro escenarios de la spec pre-audit, de punta a punta por la CLI, sobre expedientes
reales y con un proveedor falso (`httpx.MockTransport`)."""

import os
import stat
from datetime import timedelta

import pytest
from assessment.dossier.stages import AFTER, basic

from ens_evidence.assessment.dossier.sections import PRE_AUDIT_PENDING
from ens_evidence.preaudit.adapter import SCHEMA_ERROR
from ens_evidence.preaudit.redact import SECRET
from ens_evidence.preaudit.service import PREAUDITS_PREFIX

from .helpers import (
    CONTRADICTION,
    MEASURE,
    POLICY_DOCUMENT,
    FakeAnthropic,
    configure,
    prepared_sha,
    traceable,
    with_evidence,
    world_at,
)

TOKEN = "ghp_" + "Z9y8X7w6V5u4T3s2R1q0P9o8"
P = ["preaudit"]


@pytest.fixture(scope="module")
def stages():
    return basic()


@pytest.fixture
def world(stages, tmp_path, monkeypatch):
    world = world_at(stages, tmp_path, monkeypatch)
    world.at(AFTER)
    return world


def prepare_and_consent(world, dossier: str):
    world.at(AFTER + timedelta(minutes=1))
    prepared = world.run(*P, "prepare", dossier)
    assert prepared.exit_code == 0, prepared.output
    world.at(AFTER + timedelta(minutes=2))
    sent = world.run(*P, "run", dossier, "--consent", prepared_sha(prepared.output), "--by", "Ana")
    return prepared, sent


# --- Scenario: hallazgo trazable ----------------------------------------------------------------


def test_traceable_finding_cites_measure_document_and_evidence(world, tmp_path, monkeypatch):
    dossier, evidence = with_evidence(world, tmp_path, f"{CONTRADICTION}\n", document=True)
    configure(world)
    fake = FakeAnthropic(lambda payload: traceable(payload, evidence)).install(monkeypatch)

    _, sent = prepare_and_consent(world, dossier)

    assert sent.exit_code == 0, sent.output
    assert "Capa de juicio: DONE, 1 hallazgos, 0 descartados" in sent.output
    assert len(fake.requests) == 1
    shown = world.run(*P, "show", "1")
    assert shown.exit_code == 0, shown.output
    assert f"[NC_MENOR] {MEASURE} — coherencia-politica-evidencia (JUDGMENT)" in shown.output
    assert f"Cita DOCUMENT: {POLICY_DOCUMENT}" in shown.output
    assert f"Cita EVIDENCE: {evidence}" in shown.output
    assert f"«{CONTRADICTION}»" in shown.output
    assert "Fuente: CCN-CERT IC-01/19, apartado 53" in shown.output
    assert "Propuesta; la decide una persona." in shown.output
    assert "no dictamina" in shown.output


# --- Scenario: sin LLM --------------------------------------------------------------------------


def test_without_a_provider_the_deterministic_layer_completes_and_review_is_pending(world, stages):
    ran = world.run(*P, "run", stages.dossier)

    assert ran.exit_code == 0, ran.output
    assert "Capa determinista: DONE" in ran.output
    assert "Capa de juicio: NOT_RUN." in ran.output
    assert PRE_AUDIT_PENDING in ran.output
    shown = world.run(*P, "show", "1")
    assert shown.exit_code == 0 and PRE_AUDIT_PENDING in shown.output
    assert "(JUDGMENT)" not in shown.output


# --- Scenario: sin secretos ---------------------------------------------------------------------


def test_a_token_in_a_log_is_redacted_in_what_would_be_sent(world, tmp_path):
    log = f"2026-10-30T10:00:00Z backup ok\n2026-10-30T10:05:00Z push auth={TOKEN}\n"
    dossier, _ = with_evidence(world, tmp_path, log)
    configure(world)

    prepared = world.run(*P, "prepare", dossier)

    assert prepared.exit_code == 0, prepared.output
    assert TOKEN not in prepared.output and f"push {SECRET}" in prepared.output
    [payload_key] = [k for k in world.store.list(PREAUDITS_PREFIX) if k.endswith("payload.json")]
    assert TOKEN.encode() not in world.store.read_bytes(payload_key)
    mode = os.stat(world.store._root / payload_key).st_mode
    assert stat.S_IMODE(mode) == 0o600


# --- Scenario: salida inválida ------------------------------------------------------------------


def test_a_response_outside_the_schema_is_an_error_without_findings(world, tmp_path, monkeypatch):
    dossier, _ = with_evidence(world, tmp_path, f"{CONTRADICTION}\n", document=True)
    configure(world)
    invalid = {"findings": [{"criterion": "x", "verdict": "FAVORABLE"}]}
    FakeAnthropic(lambda payload: invalid).install(monkeypatch)

    _, sent = prepare_and_consent(world, dossier)

    assert sent.exit_code == 1, sent.output
    assert f"Capa de juicio: ERROR ({SCHEMA_ERROR}); sin hallazgos." in sent.output
    assert "Revisión humana pendiente" in sent.output
    shown = world.run(*P, "show", "1")
    assert "(JUDGMENT)" not in shown.output and "FAVORABLE" not in shown.output


# --- uso ---------------------------------------------------------------------------------------


def test_prepare_without_settings_is_a_configuration_error(world, stages):
    prepared = world.run(*P, "prepare", stages.dossier)

    assert prepared.exit_code == 2
    assert "configura `preaudit`" in prepared.output


def test_a_malformed_consent_is_a_usage_error(world, stages):
    ran = world.run(*P, "run", stages.dossier, "--consent", "abc", "--by", "Ana")

    assert ran.exit_code == 2 and "sha256" in ran.output
    assert world.store.list(PREAUDITS_PREFIX) == []
