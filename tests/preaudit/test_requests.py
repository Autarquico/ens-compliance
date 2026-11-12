"""Peticiones de pre-auditoría: la UI las deja, `app` las ejecuta con el servicio de la CLI.

Una huella que no es la de la preparación no crea petición; una que deja de coincidir no envía
nada; un corte entre enviar y registrar no repite el envío."""

from datetime import timedelta

import pytest
from assessment.dossier.stages import basic

from ens_evidence.preaudit.records import scan_runs
from ens_evidence.preaudit.requests import (
    EXPIRES_AFTER,
    REQUESTS_PREFIX,
    Outcome,
    RequestRejected,
    RunRequest,
    State,
    execute,
    list_requests,
    pending_keys,
    request_prepare,
    request_run,
)
from ens_evidence.preaudit.service import prepare

from .helpers import CONTRADICTION, FakeAnthropic, traceable, with_evidence, world_at
from .test_service import ANTHROPIC, PREPARED_AT, SENT_AT

EMAIL = "ana@clinica.example"
ASKED_AT = PREPARED_AT - timedelta(seconds=30)


@pytest.fixture(scope="module")
def stages():
    return basic()


@pytest.fixture
def contradicted(stages, tmp_path, monkeypatch):
    world = world_at(stages, tmp_path, monkeypatch)
    dossier, evidence = with_evidence(world, tmp_path, f"{CONTRADICTION}\n", document=True)
    fake = FakeAnthropic(lambda payload: traceable(payload, evidence)).install(monkeypatch)
    return world.store, dossier, fake


def ask_prepare(store, dossier, at=ASKED_AT):
    return request_prepare(store, dossier, by="Ana Pérez", email=EMAIL, now=at)


def ask_run(store, dossier, prepared, sha=None, at=PREPARED_AT, **kwargs):
    return request_run(
        store,
        dossier,
        payload_sha256=sha or prepared.payload.sha256,
        prepared_key=prepared.key,
        by="Ana Pérez",
        email=EMAIL,
        now=at,
        accept_shell_risk=kwargs.get("accept_shell_risk", False),
    )


def execute_now(store, key, at=SENT_AT):
    return execute(store, key, ANTHROPIC, now=lambda: at)


def test_prepare_request_is_pending_until_app_executes_it(contradicted):
    store, dossier, _ = contradicted

    created = ask_prepare(store, dossier)

    assert created.key.startswith(f"{REQUESTS_PREFIX}/{dossier}/")
    assert [(r.state, r.request.kind) for r in list_requests(store, SENT_AT)] == [
        (State.PENDING, "prepare")
    ]
    assert pending_keys(store, SENT_AT) == [created.key]

    result = execute_now(store, created.key)

    assert result.outcome is Outcome.DONE
    assert store.exists(result.prepared_key)
    assert pending_keys(store, SENT_AT) == []
    assert list_requests(store, SENT_AT)[0].state is State.DONE


def test_only_one_live_request_per_dossier(contradicted):
    store, dossier, _ = contradicted
    ask_prepare(store, dossier)

    with pytest.raises(RequestRejected, match="sin terminar"):
        ask_prepare(store, dossier)
    assert len(list_requests(store, SENT_AT)) == 1


def test_a_request_expires_after_24_hours_and_is_not_executed(contradicted):
    store, dossier, fake = contradicted
    old = ask_prepare(store, dossier)
    later = ASKED_AT + EXPIRES_AFTER

    assert pending_keys(store, later) == []
    assert list_requests(store, later)[0].state is State.EXPIRED
    assert execute_now(store, old.key, at=later) is None
    renewed = ask_prepare(store, dossier, at=later)

    assert [r.state for r in list_requests(store, later)] == [State.EXPIRED, State.PENDING]
    assert renewed.key.endswith("000002.json")


def test_run_request_executes_the_service_with_the_consented_hash(contradicted):
    store, dossier, fake = contradicted
    prepared = prepare(store, dossier, None, ANTHROPIC, now=PREPARED_AT)
    created = ask_run(store, dossier, prepared)

    result = execute_now(store, created.key)

    assert result.outcome is Outcome.DONE, result.message
    [record] = scan_runs(store).records
    assert result.run_key == record.key
    assert record.record.run.consent.payload_sha256 == prepared.payload.sha256
    assert record.record.run.consent.by == "Ana Pérez"
    assert len(fake.requests) == 1


def test_run_request_with_another_hash_is_not_created(contradicted):
    store, dossier, fake = contradicted
    prepared = prepare(store, dossier, None, ANTHROPIC, now=PREPARED_AT)

    with pytest.raises(RequestRejected, match="no es la de esa preparación"):
        ask_run(store, dossier, prepared, sha="0" * 64)
    with pytest.raises(RequestRejected, match="no es la de esa preparación"):
        ask_run(store, dossier, prepared, sha="f" * 64)
    assert list_requests(store, SENT_AT) == []
    assert fake.requests == []


def test_run_request_citing_another_dossiers_preparation_is_not_created(contradicted):
    store, dossier, _ = contradicted
    prepared = prepare(store, dossier, None, ANTHROPIC, now=PREPARED_AT)
    other = "20990101T000000000000Z-" + "a" * 12

    with pytest.raises(RequestRejected, match="no es la de esa preparación"):
        request_run(
            store,
            other,
            payload_sha256=prepared.payload.sha256,
            prepared_key=prepared.key,
            by="Ana",
            email=EMAIL,
            now=PREPARED_AT,
            accept_shell_risk=False,
        )


def test_a_hash_that_stops_matching_sends_nothing_and_records_the_error(contradicted):
    store, dossier, fake = contradicted
    prepared = prepare(store, dossier, None, ANTHROPIC, now=PREPARED_AT)
    forged = RunRequest(
        dossier=dossier,
        by="Ana",
        email=EMAIL,
        requested_at=PREPARED_AT,
        payload_sha256="0" * 64,
        prepared_key=prepared.key,
        accept_shell_risk=False,
    )
    key = f"{REQUESTS_PREFIX}/{dossier}/000001.json"
    store.create_bytes(key, forged.model_dump_json().encode())

    result = execute_now(store, key)

    assert result.outcome is Outcome.ERROR
    assert "no coincide" in result.message
    assert fake.requests == []
    assert scan_runs(store).records == ()
    assert list_requests(store, SENT_AT)[0].state is State.ERROR


def test_a_rejection_of_the_service_is_the_result_and_is_not_retried(contradicted):
    store, dossier, fake = contradicted
    created = ask_prepare(store, dossier)

    result = execute(store, created.key, None, now=lambda: SENT_AT)

    assert result.outcome is Outcome.ERROR
    assert "preaudit" in result.message
    assert execute_now(store, created.key) is None
    assert pending_keys(store, SENT_AT) == []


def test_a_cut_after_claiming_never_executes_again(contradicted):
    store, dossier, fake = contradicted
    prepared = prepare(store, dossier, None, ANTHROPIC, now=PREPARED_AT)
    created = ask_run(store, dossier, prepared)
    store.create_bytes(created.key.replace(".json", ".started.json"), b"{}\n")

    assert list_requests(store, SENT_AT)[0].state is State.RUNNING
    assert pending_keys(store, SENT_AT) == [created.key]
    result = execute_now(store, created.key)

    assert result.outcome is Outcome.ERROR
    assert "interrumpió" in result.message
    assert fake.requests == []
    assert pending_keys(store, SENT_AT) == []


def test_invalid_identity_is_rejected_without_writing(contradicted):
    store, dossier, _ = contradicted

    with pytest.raises(RequestRejected):
        request_prepare(store, dossier, by="Ana", email="no es un email", now=ASKED_AT)
    with pytest.raises(RequestRejected):
        request_prepare(store, "../x", by="Ana", email=EMAIL, now=ASKED_AT)
    assert store.list(REQUESTS_PREFIX) == []
