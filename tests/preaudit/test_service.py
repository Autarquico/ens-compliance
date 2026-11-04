"""Servicio de la pre-auditoría: el consentimiento liga huella y destino, sin él no hay red, y
cada ejecución se añade sin sustituir a ninguna."""

import json
from datetime import timedelta

import pytest
import yaml
from assessment.dossier.stages import AFTER, basic

from ens_evidence.models.preaudit import DiscardReason, Layer, LayerStatus, Provider
from ens_evidence.preaudit import service
from ens_evidence.preaudit.service import (
    PREAUDITS_PREFIX,
    RUNS_PREFIX,
    PreAuditRejected,
    PreAuditUnconfigured,
    prepare,
    run,
    scan_runs,
)
from ens_evidence.workspace import PreAuditSettings, SettingsError, load_settings

from .helpers import (
    CONTRADICTION,
    MODEL,
    FakeAnthropic,
    traceable,
    with_evidence,
    world_at,
)

ANTHROPIC = PreAuditSettings(provider=Provider.ANTHROPIC, model=MODEL)
PREPARED_AT = AFTER + timedelta(minutes=1)
SENT_AT = AFTER + timedelta(minutes=2)


@pytest.fixture(scope="module")
def stages():
    return basic()


@pytest.fixture
def world(stages, tmp_path, monkeypatch):
    return world_at(stages, tmp_path, monkeypatch)


@pytest.fixture
def contradicted(world, tmp_path, monkeypatch):
    """Expediente con una política contradicha por una evidencia, y el proveedor falso."""
    dossier, evidence = with_evidence(world, tmp_path, f"{CONTRADICTION}\n", document=True)
    fake = FakeAnthropic(lambda payload: traceable(payload, evidence)).install(monkeypatch)
    return world.store, dossier, fake


def send(store, dossier, sha, *, settings=ANTHROPIC, at=SENT_AT, **kwargs):
    return run(store, dossier, None, sha, "Ana", settings=settings, now=at, **kwargs)


def run_keys(store) -> list[str]:
    return store.list(RUNS_PREFIX)


# --- preparar ----------------------------------------------------------------------------------


def test_prepare_records_fingerprint_destination_and_pack_and_sends_nothing(contradicted):
    store, dossier, fake = contradicted

    prepared = prepare(store, dossier, None, ANTHROPIC, now=PREPARED_AT)

    record = json.loads(store.read_bytes(prepared.key))
    assert record["payload_sha256"] == prepared.payload.sha256
    assert record["destination"] == "anthropic https://api.anthropic.com"
    assert record["pack_version"] == "1.0.0" and record["dossier"] == dossier
    assert store.read_bytes(record["payload"]) == prepared.payload.canonical
    assert prepared.key.startswith(f"{PREAUDITS_PREFIX}/")
    assert prepared.payload.sha256[:12] in prepared.key
    assert fake.requests == [] and run_keys(store) == []


def test_prepare_needs_a_configured_model(contradicted):
    store, dossier, _ = contradicted

    with pytest.raises(PreAuditUnconfigured):
        prepare(store, dossier, None, None, now=PREPARED_AT)
    with pytest.raises(PreAuditUnconfigured, match="openai"):
        prepare(store, dossier, Provider.OPENAI, ANTHROPIC, now=PREPARED_AT)


def test_a_dossier_that_no_longer_reproduces_is_not_prepared(contradicted):
    store, dossier, _ = contradicted
    key = "org/docs/politica-seguridad.md"
    store.write_text(key, store.read_text(key) + "\nAñadido después.\n")

    with pytest.raises(PreAuditRejected) as raised:
        prepare(store, dossier, None, ANTHROPIC, now=PREPARED_AT)

    assert "no está íntegro o ya no describe el estado actual" in raised.value.errors[0]
    assert store.list(PREAUDITS_PREFIX) == []


def test_what_the_preaudit_writes_does_not_alter_the_dossier(contradicted):
    store, dossier, _ = contradicted
    prepared = prepare(store, dossier, None, ANTHROPIC, now=PREPARED_AT)
    send(store, dossier, prepared.payload.sha256)

    assert service.dossier_problems(store, dossier) == []


# --- consentimiento ----------------------------------------------------------------------------


def test_a_payload_that_changed_since_it_was_prepared_is_not_sent(contradicted):
    store, dossier, fake = contradicted
    prepared = prepare(store, dossier, None, ANTHROPIC, now=PREPARED_AT)
    payload_key = json.loads(store.read_bytes(prepared.key))["payload"]
    store.write_bytes(payload_key, prepared.payload.canonical + b" ")

    with pytest.raises(PreAuditRejected, match="ha cambiado"):
        send(store, dossier, prepared.payload.sha256)

    assert fake.requests == [] and run_keys(store) == []


def test_consent_needs_a_declared_name(contradicted):
    store, dossier, fake = contradicted
    prepared = prepare(store, dossier, None, ANTHROPIC, now=PREPARED_AT)

    with pytest.raises(PreAuditRejected, match="--by"):
        run(store, dossier, None, prepared.payload.sha256, None, settings=ANTHROPIC, now=SENT_AT)

    assert fake.requests == []


def test_consent_is_recorded_with_who_when_and_where(contradicted):
    store, dossier, fake = contradicted
    prepared = prepare(store, dossier, None, ANTHROPIC, now=PREPARED_AT)

    result = send(store, dossier, prepared.payload.sha256)

    consent = result.record.run.consent
    assert consent is not None and consent.by == "Ana" and consent.at == SENT_AT
    assert consent.payload_sha256 == prepared.payload.sha256
    assert consent.destination == "anthropic https://api.anthropic.com"
    assert result.record.prepared == prepared.key
    [request] = fake.requests
    assert request.content.count(b'"temperature"') == 0


def test_consent_is_on_record_before_anything_leaves(contradicted):
    store, dossier, fake = contradicted
    prepared = prepare(store, dossier, None, ANTHROPIC, now=PREPARED_AT)
    directory = prepared.key.rsplit("/", 1)[0]
    on_record_when_sent: list[list[str]] = []
    reply = fake.reply

    def observing(payload):
        on_record_when_sent.append([k for k in store.list(directory) if "/consent-" in k])
        return reply(payload)

    fake.reply = observing
    send(store, dossier, prepared.payload.sha256)

    [[key]] = on_record_when_sent
    assert key.startswith(f"{directory}/consent-")
    consent = json.loads(store.read_bytes(key))
    assert consent["format"] == service.CONSENT_FORMAT
    assert consent["by"] == "Ana" and consent["payload_sha256"] == prepared.payload.sha256
    assert consent["destination"] == "anthropic https://api.anthropic.com"


def test_a_rejected_consent_leaves_no_record_of_consent(contradicted):
    store, dossier, fake = contradicted
    prepared = prepare(store, dossier, None, ANTHROPIC, now=PREPARED_AT)
    elsewhere = ANTHROPIC.model_copy(update={"base_url": "https://gateway.example.com"})

    with pytest.raises(PreAuditRejected):
        send(store, dossier, prepared.payload.sha256, settings=elsewhere)

    assert not [k for k in store.list(PREAUDITS_PREFIX) if "/consent-" in k]


@pytest.mark.parametrize(
    "unsafe",
    [
        {"reason": "Cambia el título del terminal\x1b]0;aprobado\x07."},
        {"reason": "Borra la línea\r con un retorno de carro."},
        {"reason": "x" * 2001},
    ],
)
def test_model_text_that_cannot_be_shown_as_is_discards_the_finding(contradicted, unsafe):
    store, dossier, fake = contradicted
    reply = fake.reply
    fake.reply = lambda payload: {"findings": [reply(payload)["findings"][0] | unsafe]}
    prepared = prepare(store, dossier, None, ANTHROPIC, now=PREPARED_AT)

    result = send(store, dossier, prepared.payload.sha256)

    assert result.record.run.judgment_status is LayerStatus.DONE
    assert result.record.run.discards == {DiscardReason.UNDISPLAYABLE_TEXT: 1}
    assert not [f for f in result.record.findings if f.layer is Layer.JUDGMENT]


# --- solo adición ------------------------------------------------------------------------------


def test_rerunning_never_replaces_an_earlier_run(contradicted, monkeypatch):
    store, dossier, fake = contradicted
    prepared = prepare(store, dossier, None, ANTHROPIC, now=PREPARED_AT)
    first = send(store, dossier, prepared.payload.sha256)
    fake.reply = lambda payload: {"findings": [{"verdict": "FAVORABLE"}]}

    failed = send(store, dossier, prepared.payload.sha256, at=SENT_AT + timedelta(minutes=1))
    later = SENT_AT + timedelta(minutes=2)
    empty = run(store, dossier, None, None, None, settings=ANTHROPIC, now=later)

    assert failed.record.run.judgment_status is LayerStatus.ERROR
    assert empty.record.run.judgment_status is LayerStatus.NOT_RUN
    scan = scan_runs(store)
    assert scan.problems == ()
    assert [s.record.seq for s in scan.records] == [1, 2, 3]
    assert scan.records[0].record == first.record
    assert any(f.layer is Layer.JUDGMENT for f in service.show(store, 1).record.findings)
    assert scan.records[1].record.previous_sha256 == scan.records[0].sha256


def test_a_broken_run_history_blocks_with_a_visible_error(contradicted):
    store, dossier, _ = contradicted
    run(store, dossier, None, None, None, settings=None, now=SENT_AT)
    [record] = [k for k in run_keys(store) if not k.startswith(f"{RUNS_PREFIX}/seq/")]
    store.write_bytes(record, store.read_bytes(record).replace(b"NOT_RUN", b"DONE"))

    with pytest.raises(PreAuditRejected, match="roto"):
        run(store, dossier, None, None, None, settings=None, now=SENT_AT + timedelta(minutes=1))
    with pytest.raises(PreAuditRejected, match="roto"):
        service.show(store, 1)


def test_a_run_recorded_in_the_future_blocks(contradicted):
    store, dossier, _ = contradicted
    run(store, dossier, None, None, None, settings=None, now=SENT_AT)

    with pytest.raises(PreAuditRejected, match="futuro"):
        run(store, dossier, None, None, None, settings=None, now=SENT_AT - timedelta(minutes=1))


# --- ajustes -----------------------------------------------------------------------------------


def write_settings(tmp_path, preaudit: dict):
    path = tmp_path / "settings.yaml"
    workspace = {"backend": "local", "local": {"path": str(tmp_path / "ws")}}
    path.write_text(yaml.safe_dump({"workspace": workspace, "preaudit": preaudit}))
    return path


def test_preaudit_settings_load_from_the_workspace_settings_file(tmp_path):
    path = write_settings(tmp_path, {"provider": "claude-code", "model": MODEL, "timeout": 60})

    loaded = load_settings(path, allow_inside_product=True).preaudit

    assert loaded == PreAuditSettings(provider=Provider.CLAUDE_CODE, model=MODEL, timeout=60)


@pytest.mark.parametrize(
    "preaudit",
    [
        {"provider": "anthropic"},
        {"provider": "anthropic", "model": " "},
        {"provider": "codex", "model": MODEL, "base_url": "https://x.example.com"},
        {"provider": "openai", "model": MODEL, "base_url": "http://api.openai.com"},
        {"provider": "openai", "model": MODEL, "api_key": "sk-nunca"},
        {"provider": "local", "model": MODEL},
        {"provider": "openai", "model": MODEL, "timeout": 0},
    ],
)
def test_invalid_preaudit_settings_are_rejected(tmp_path, preaudit):
    with pytest.raises(SettingsError):
        load_settings(write_settings(tmp_path, preaudit), allow_inside_product=True)
