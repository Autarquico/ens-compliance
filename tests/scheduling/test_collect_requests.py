"""Peticiones de recogida: la UI las deja, `app` recoge ese conector y evalúa sus checks.

Un fallo de recogida es una evidencia en ERROR y los checks se evalúan igual; un corte tras
reclamar no repite la recogida; el resultado nunca lleva texto del conector."""

import json
import typing
from datetime import timedelta

import pytest
from checks.test_engine import NOW, collect

from ens_evidence.checks.engine import run_all
from ens_evidence.checks.store import scan_runs
from ens_evidence.connectors.config import connector_config_key
from ens_evidence.models.enums import CheckStatus, CollectionStatus
from ens_evidence.scheduling import collect_requests as module
from ens_evidence.scheduling.collect_requests import (
    EXPIRES_AFTER,
    REQUESTS_PREFIX,
    ChecksOutcome,
    CollectRequestResult,
    ErrorCode,
    Outcome,
    RequestRejected,
    State,
    execute,
    list_requests,
    pending_keys,
    request_collect,
    requests_of,
)
from ens_evidence.scheduling.config import CollectConfig
from ens_evidence.scope.approval import APPROVAL_KEY
from ens_evidence.workspace import AlreadyExists
from scheduling.collect_fakes import CANARY
from scheduling.fakes import FakeClock

EMAIL = "ana@clinica.example"
ASKED_AT = NOW + timedelta(days=1)
FIRST = f"{REQUESTS_PREFIX}/github/000001.json"
CHECK_KEY = "evidence/checks/github/000001-20261030T120000000000Z-0123456789ab.json"

CONFIRMED = '{"confirmed_at": "2026-11-09T00:00:00+00:00"}\n'


def ask(store, at=ASKED_AT, connector="github"):
    return request_collect(store, connector, email=EMAIL, now=at)


def run(store, settings, plugins, at=ASKED_AT, key=FIRST):
    return execute(
        store,
        settings,
        key,
        plugins=plugins,
        cfg=CollectConfig(),
        clock=FakeClock(at),
    )


def requests_listed(store):
    """Las claves bajo `collect-requests` sin el fichero del lock por conector."""
    return [key for key in store.list(REQUESTS_PREFIX) if not key.endswith(".lock")]


def stored_result(store, number=1, connector="github"):
    key = f"{REQUESTS_PREFIX}/{connector}/{number:06d}.result.json"
    return CollectRequestResult.model_validate_json(store.read_bytes(key))


def write_raw(store, name, document, connector="github"):
    store.write_text(f"{REQUESTS_PREFIX}/{connector}/{name}", json.dumps(document))


def request_document(**changes):
    return {
        "format": "ens-collect-request/1",
        "connector": "github",
        "email": EMAIL,
        "requested_at": ASKED_AT.isoformat(),
    } | changes


def result_document(**changes):
    return {
        "format": "ens-collect-request-result/1",
        "outcome": "DONE",
        "at": ASKED_AT.isoformat(),
        "attempts": 1,
        "checks": "RECORDED",
    } | changes


def last_statuses(store):
    last = scan_runs(store).last("github")
    return {r.status for r in last.run.results}


# --- crear -------------------------------------------------------------------------------------


def test_asking_leaves_a_pending_request(store):
    created = ask(store)

    assert created.key == FIRST
    assert created.state is State.PENDING
    assert [(r.state, r.request.email) for r in requests_of(store, "github", ASKED_AT)] == [
        (State.PENDING, EMAIL)
    ]
    assert pending_keys(store, ASKED_AT) == [FIRST]
    assert requests_listed(store) == [FIRST]


@pytest.mark.parametrize("connector", ["../x", "GitHub", "git/hub", "", "restic"])
def test_an_unknown_or_unconfigured_connector_is_rejected_without_writing(store, connector):
    with pytest.raises(RequestRejected):
        ask(store, connector=connector)

    assert requests_listed(store) == []


def test_an_identity_that_is_not_an_email_is_rejected(store):
    with pytest.raises(RequestRejected):
        request_collect(store, "github", email="no-es-email", now=ASKED_AT)


def test_only_one_request_is_alive_per_connector(store):
    ask(store)

    with pytest.raises(RequestRejected, match="sin terminar"):
        ask(store, ASKED_AT + timedelta(seconds=5))

    assert requests_listed(store) == [FIRST]


def test_a_claimed_request_is_still_alive(store):
    ask(store)
    store.write_text(f"{REQUESTS_PREFIX}/github/000001.started.json", "{}\n")

    with pytest.raises(RequestRejected):
        ask(store, ASKED_AT + timedelta(seconds=5))
    assert requests_of(store, "github", ASKED_AT)[0].state is State.RUNNING


def test_a_simulated_race_loses_on_exclusive_creation(store, monkeypatch):
    def lost(key, data):
        raise AlreadyExists(key)

    monkeypatch.setattr(store, "create_bytes", lost)

    with pytest.raises(RequestRejected, match="a la vez"):
        ask(store)


def test_a_new_request_can_be_asked_once_the_previous_is_done(store, settings, plugins, fake):
    ask(store)
    run(store, settings, plugins)

    again = ask(store, ASKED_AT + timedelta(minutes=5))

    assert again.key == f"{REQUESTS_PREFIX}/github/000002.json"


# --- caducidad ---------------------------------------------------------------------------------


def test_an_unclaimed_request_expires_at_24_hours_and_is_not_pending(store):
    ask(store)
    just_before = ASKED_AT + EXPIRES_AFTER - timedelta(seconds=1)
    at_limit = ASKED_AT + EXPIRES_AFTER

    assert requests_of(store, "github", just_before)[0].state is State.PENDING
    assert pending_keys(store, just_before) == [FIRST]
    assert requests_of(store, "github", at_limit)[0].state is State.EXPIRED
    assert pending_keys(store, at_limit) == []


def test_an_expired_request_is_not_executed_and_a_new_one_can_be_asked(
    store, settings, plugins, fake
):
    ask(store)
    late = ASKED_AT + EXPIRES_AFTER + timedelta(seconds=1)

    assert run(store, settings, plugins, at=late) is None
    assert requests_listed(store) == [FIRST]
    assert ask(store, late).key == f"{REQUESTS_PREFIX}/github/000002.json"


def test_a_claimed_request_past_24_hours_is_closed_as_interrupted(store, settings, plugins, fake):
    ask(store)
    store.write_text(f"{REQUESTS_PREFIX}/github/000001.started.json", CONFIRMED)
    late = ASKED_AT + EXPIRES_AFTER + timedelta(hours=3)

    assert requests_of(store, "github", late)[0].state is State.RUNNING
    result = run(store, settings, plugins, at=late)

    assert (result.outcome, result.code, result.checks) == (
        Outcome.ERROR,
        ErrorCode.INTERRUPTED,
        ChecksOutcome.NOT_RUN,
    )
    assert not any("manifests" in key for key in store.list("evidence"))


# --- ejecutar ----------------------------------------------------------------------------------


def test_execute_collects_one_connector_and_records_the_checks(store, settings, plugins, fake):
    ask(store)

    result = run(store, settings, plugins)

    assert (result.outcome, result.collection_status) == (Outcome.DONE, CollectionStatus.SUCCESS)
    assert result.attempts == 1
    assert result.evidence_id is not None
    assert result.checks is ChecksOutcome.RECORDED
    assert result.check_key in store.list("evidence/checks/github")
    assert stored_result(store) == result
    assert store.exists(f"{REQUESTS_PREFIX}/github/000001.started.json")
    status = list_requests(store, ASKED_AT)[0]
    assert (status.state, status.result) == (State.DONE, result)
    assert pending_keys(store, ASKED_AT) == []
    assert run(store, settings, plugins) is None


def test_a_failed_collection_is_done_and_the_checks_still_run(store, settings, plugins, fake):
    collect(store)
    run_all(store, NOW)
    assert CheckStatus.PASS in last_statuses(store)
    fake("collect")
    ask(store)

    result = run(store, settings, plugins)

    assert result.outcome is Outcome.DONE
    assert result.collection_status is CollectionStatus.ERROR
    assert (result.phase.value, result.error_type) == ("collect", "RuntimeError")
    assert result.attempts == 1
    assert result.checks is ChecksOutcome.RECORDED
    assert CheckStatus.PASS not in last_statuses(store)


def test_an_invalid_config_still_runs_the_checks_and_drops_the_previous_pass(
    store, settings, plugins, fake
):
    collect(store)
    run_all(store, NOW)
    assert CheckStatus.PASS in last_statuses(store)
    store.write_text(connector_config_key("github"), "asset_id: [sin cerrar")
    ask(store)

    result = run(store, settings, plugins)

    assert (result.outcome, result.code) == (Outcome.ERROR, ErrorCode.CONFIG_INVALID)
    assert result.evidence_id is None
    assert result.checks is ChecksOutcome.RECORDED
    assert CheckStatus.PASS not in last_statuses(store)


def test_an_unexpected_checks_failure_keeps_what_was_collected(
    store, settings, plugins, fake, monkeypatch
):
    def boom(*args, **kwargs):
        raise RuntimeError("secreto ghp_" + "c" * 30)

    monkeypatch.setattr(module, "run_one", boom)
    ask(store)

    result = run(store, settings, plugins)

    assert (result.outcome, result.collection_status) == (Outcome.DONE, CollectionStatus.SUCCESS)
    assert result.evidence_id is not None
    assert result.attempts == 1
    assert (result.checks, result.check_key) == (ChecksOutcome.ERROR, None)
    assert "ghp_" not in store.read_bytes(f"{REQUESTS_PREFIX}/github/000001.result.json").decode()


def test_a_failure_writing_the_result_propagates(store, settings, plugins, fake, monkeypatch):
    create = store.create_bytes

    def refuse_result(key, data):
        if key.endswith(".result.json"):
            raise OSError("disco lleno")
        create(key, data)

    ask(store)
    monkeypatch.setattr(store, "create_bytes", refuse_result)

    with pytest.raises(OSError):
        run(store, settings, plugins)


def test_a_claim_without_result_is_closed_as_interrupted_without_collecting(
    store, settings, plugins, fake
):
    ask(store)
    store.write_text(f"{REQUESTS_PREFIX}/github/000001.started.json", "{}\n")

    result = run(store, settings, plugins)

    assert (result.outcome, result.code) == (Outcome.ERROR, ErrorCode.INTERRUPTED)
    assert not (fake.state / "calls").exists()
    assert stored_result(store) == result


def test_pending_keys_are_oldest_first_across_connectors(store):
    ask(store)
    write_raw(
        store,
        "000001.json",
        request_document(
            connector="aaa-first-by-name",
            requested_at=(ASKED_AT + timedelta(minutes=5)).isoformat(),
        ),
        connector="aaa-first-by-name",
    )

    assert pending_keys(store, ASKED_AT + timedelta(minutes=6)) == [
        FIRST,
        f"{REQUESTS_PREFIX}/aaa-first-by-name/000001.json",
    ]


def test_the_scope_not_approved_skips_the_checks_but_the_collection_is_imported(
    store, settings, plugins, fake
):
    (store._root / APPROVAL_KEY).unlink()
    ask(store)

    result = run(store, settings, plugins)

    assert result.checks is ChecksOutcome.SKIPPED_SCOPE
    assert (result.outcome, result.collection_status) == (Outcome.DONE, CollectionStatus.SUCCESS)
    assert result.evidence_id is not None


def test_an_unconfigured_connector_is_closed_without_collecting(store, settings, plugins, fake):
    ask(store)
    (store._root / connector_config_key("github")).unlink()

    result = run(store, settings, plugins)

    assert (result.code, result.checks) == (ErrorCode.NOT_CONFIGURED, ChecksOutcome.NOT_RUN)
    assert not (fake.state / "calls").exists()


def test_a_connector_that_is_not_installed_is_unknown_but_checks_run(store, settings, fake):
    from ens_evidence.connectors.registry import ConnectorRegistry
    from ens_evidence.scheduling.collect import Plugins

    ask(store)

    result = run(store, settings, Plugins(ConnectorRegistry()))

    assert (result.outcome, result.code) == (Outcome.ERROR, ErrorCode.CONNECTOR_UNKNOWN)
    assert result.checks is ChecksOutcome.RECORDED


def test_a_collection_runs_with_a_single_attempt(store, settings, plugins, fake):
    fake("collect")
    ask(store)
    clock = FakeClock(ASKED_AT)

    result = execute(
        store, settings, FIRST, plugins=plugins, cfg=CollectConfig(retries=3), clock=clock
    )

    assert result.attempts == 1
    assert clock.sleeps == []


def test_the_connector_error_message_reaches_no_file_of_the_request(store, settings, plugins, fake):
    fake("prepare")
    ask(store)

    result = run(store, settings, plugins)

    assert result.collection_status is CollectionStatus.ERROR
    assert (result.phase.value, result.error_type) == ("prepare", "RuntimeError")
    for key in requests_listed(store):
        text = store.read_bytes(key).decode()
        assert CANARY not in text
        assert "ghp_" not in text


# --- lectura hostil ----------------------------------------------------------------------------


def test_a_request_of_another_connector_is_unreadable_and_not_executed(
    store, settings, plugins, fake
):
    write_raw(store, "000001.json", request_document(connector="restic"))

    [status] = requests_of(store, "github", ASKED_AT)

    assert (status.state, status.request, status.result.code) == (
        State.DONE,
        None,
        ErrorCode.UNREADABLE,
    )
    assert pending_keys(store, ASKED_AT) == []
    assert run(store, settings, plugins) is None
    assert not (fake.state / "calls").exists()
    assert ask(store, ASKED_AT).key == f"{REQUESTS_PREFIX}/github/000002.json"


def test_a_malformed_request_is_unreadable_and_not_alive(store):
    store.write_text(FIRST, "{no es json")

    [status] = requests_of(store, "github", ASKED_AT)

    assert (status.state, status.result.code) == (State.DONE, ErrorCode.UNREADABLE)
    assert pending_keys(store, ASKED_AT) == []


HOSTILE = [
    {"evidence_id": "../../etc/passwd"},
    {"evidence_id": "EV-20261030-<script>"},
    {"check_key": "evidence/checks/github/../../../secret"},
    {"check_key": "<script>alert(1)</script>"},
    {"check_key": "evidence/checks/restic/000001-20261030T120000000000Z-0123456789ab.json"},
]


@pytest.mark.parametrize("fields", HOSTILE)
def test_a_hostile_identifier_in_a_result_reads_back_as_unreadable(store, fields):
    ask(store)
    write_raw(store, "000001.result.json", result_document(**fields))

    [status] = requests_of(store, "github", ASKED_AT)

    assert status.state is State.DONE
    assert (status.result.code, status.result.evidence_id, status.result.check_key) == (
        ErrorCode.UNREADABLE,
        None,
        None,
    )


def test_canonical_identifiers_of_the_same_connector_read_back_intact(store):
    ask(store)
    write_raw(
        store,
        "000001.result.json",
        result_document(evidence_id="EV-20261030-0123abcd", check_key=CHECK_KEY),
    )

    [status] = requests_of(store, "github", ASKED_AT)

    assert (status.result.evidence_id, status.result.check_key) == (
        "EV-20261030-0123abcd",
        CHECK_KEY,
    )


@pytest.mark.parametrize(
    "fields",
    [{"evidence_id": "../x"}, {"check_key": "../x"}, {"error_type": "ruta/../con espacios"}],
)
def test_a_non_canonical_value_cannot_be_built(fields):
    with pytest.raises(ValueError):
        CollectRequestResult.model_validate(result_document(**fields))


def test_a_check_key_of_another_connector_cannot_be_written(store):
    ask(store)
    foreign = CollectRequestResult.model_validate(
        result_document(
            check_key="evidence/checks/restic/000001-20261030T120000000000Z-0123456789ab.json"
        )
    )

    with pytest.raises(ValueError):
        module._finish(store, "github", 1, foreign)
    assert not store.exists(f"{REQUESTS_PREFIX}/github/000001.result.json")


# --- sin texto libre ---------------------------------------------------------------------------


def test_the_result_has_no_free_text_field():
    text_fields = set()
    for name, field in CollectRequestResult.model_fields.items():
        args = typing.get_args(field.annotation) or (field.annotation,)
        if str in args:
            text_fields.add(name)

    assert text_fields == {"evidence_id", "check_key", "error_type"}
    assert CollectRequestResult.model_fields["error_type"].metadata
    assert "message" not in CollectRequestResult.model_fields


def test_cada_resultado_del_motor_tiene_su_resultado_de_comprobaciones():
    from ens_evidence.checks.engine import OutcomeKind

    assert set(module.CHECKS_OF_OUTCOME) == set(OutcomeKind)
