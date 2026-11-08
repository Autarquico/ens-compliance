"""Entradas hostiles contra la app HTTP: cada una recibe su respuesta y no escribe nada."""

import gzip
import json
from datetime import timedelta

import pytest

from ens_evidence.evidence import list_manifests

from .protocol_support import (
    LAPTOP,
    OS_VERSION_ROW,
    SERVER,
    SERVER_HOST,
    SERVER_UUID,
    STRANGER_UUID,
    snapshot_event,
)

JSON = {"content-type": "application/json"}
NODE_INVALID = {"node_invalid": True}
SMALL_LIMIT = 64 * 1024
LARGE_LIMIT = 10 * 1024 * 1024
LIMITS = {
    "enroll": SMALL_LIMIT,
    "config": SMALL_LIMIT,
    "distributed/read": SMALL_LIMIT,
    "log": LARGE_LIMIT,
    "distributed/write": LARGE_LIMIT,
}


def manifests(world) -> list:
    return list_manifests(world.workspace).manifests


def send_log(world, node_key, events):
    return world.post("log", {"node_key": node_key, "log_type": "result", "data": events})


def assert_ignored(world, response, before):
    assert (response.status_code, response.json()) == (200, {"node_invalid": False})
    assert world.files() == before
    assert manifests(world) == []


@pytest.fixture
def node_key(world) -> str:
    return world.enroll()


# --- tamaño y forma del cuerpo --------------------------------------------------------------


@pytest.mark.parametrize("endpoint", LIMITS)
def test_body_declared_over_the_limit_is_413(world, node_key, endpoint):
    before = world.files()
    padding = b"x" * LIMITS[endpoint]

    response = world.client.post(
        f"/agent/{endpoint}", content=b'{"a":"' + padding + b'"}', headers=JSON
    )

    assert (response.status_code, response.json()) == (413, {"error": "too_large"})
    assert world.files() == before


@pytest.mark.parametrize("endpoint", LIMITS)
def test_streamed_body_over_the_limit_is_413(world, node_key, endpoint):
    before = world.files()

    def chunks():
        yield b'{"a":"'
        yield b"x" * LIMITS[endpoint]
        yield b'"}'

    response = world.client.post(f"/agent/{endpoint}", content=chunks(), headers=JSON)

    assert (response.status_code, response.json()) == (413, {"error": "too_large"})
    assert world.files() == before


@pytest.mark.parametrize("endpoint", LIMITS)
def test_gzip_content_encoding_is_415(world, node_key, endpoint):
    before = world.files()
    body = gzip.compress(json.dumps({"node_key": node_key}).encode())

    response = world.client.post(
        f"/agent/{endpoint}", content=body, headers=JSON | {"content-encoding": "gzip"}
    )

    assert (response.status_code, response.json()) == (415, {"error": "unsupported_encoding"})
    assert world.files() == before


@pytest.mark.parametrize(
    ("raw", "headers", "status"),
    [
        (b"esto no es json", JSON, 400),
        (b"", JSON, 400),
        (b'{"node_key": NaN}', JSON, 400),
        (b'{"node_key": "\xff\xfe"}', JSON, 400),
        (b'{"node_key": "x"', JSON, 400),
        (b"[" * 60_000, JSON, 400),
        (b"[" * 40 + b"]" * 40, JSON, 400),
        (b'{"node_key": "x"}', {"content-type": "text/plain"}, 415),
        (b'{"node_key": "x"}', {}, 415),
    ],
    ids=[
        "texto",
        "vacío",
        "NaN",
        "utf8-roto",
        "truncado",
        "anidado-60k",
        "anidado-40",
        "text",
        "sin-tipo",
    ],
)
@pytest.mark.parametrize("endpoint", LIMITS)
def test_unreadable_bodies_are_rejected_with_http_errors(
    world, node_key, endpoint, raw, headers, status
):
    before = world.files()

    response = world.client.post(f"/agent/{endpoint}", content=raw, headers=headers)

    assert response.status_code == status
    assert set(response.json()) == {"error"}
    assert world.files() == before


@pytest.mark.parametrize("body", [[], "node_key", 7, None, [{"node_key": "x"}]])
def test_valid_json_that_is_not_an_object_is_node_invalid(world, node_key, body):
    before = world.files()

    response = world.client.post("/agent/config", content=json.dumps(body), headers=JSON)

    assert (response.status_code, response.json()) == (200, NODE_INVALID)
    assert world.files() == before


# --- credenciales mal formadas -----------------------------------------------------------------


@pytest.mark.parametrize(
    "forged",
    [
        "",
        "v1.",
        "v1...",
        "v1.%%%.1.AAAA",
        "v1.!!!!.1.!!!!",
        "v1.NmYxYzJkM2UtNGE1Yi00YzZkLThlOWYtMGExYjJjM2Q0ZTVm.1.",
        "v1.NmYxYzJkM2UtNGE1Yi00YzZkLThlOWYtMGExYjJjM2Q0ZTVm.-1.AAAA",
        "v1.NmYxYzJkM2UtNGE1Yi00YzZkLThlOWYtMGExYjJjM2Q0ZTVm.99999999999999999999.AAAA",
        "v2.AAAA.1.AAAA",
        "x" * 60_000,
        "v1." + "A" * 60_000,
        "\u0000‮\n",
        123,
        ["v1"],
        {"node_key": "v1"},
        None,
        True,
    ],
)
@pytest.mark.parametrize("endpoint", ["config", "log", "distributed/read", "distributed/write"])
def test_malformed_node_key_is_node_invalid(world, node_key, endpoint, forged):
    before = world.files()
    body = {"node_key": forged, "log_type": "result", "data": [], "queries": {}}

    response = world.post(endpoint, body)

    assert (response.status_code, response.json()) == (200, NODE_INVALID)
    assert world.files() == before


def test_node_key_of_a_generation_the_registry_never_issued_is_node_invalid(world, node_key):
    before = world.files()
    future = world.credentials(generation=2).node_key

    for endpoint in ("config", "log", "distributed/read"):
        assert world.post(endpoint, {"node_key": future}).json() == NODE_INVALID

    assert world.files() == before


def test_node_key_of_a_host_outside_the_inventory_is_node_invalid(world, node_key):
    before = world.files()
    stranger = world.credentials(uuid=STRANGER_UUID).node_key

    assert world.post("config", {"node_key": stranger}).json() == NODE_INVALID
    assert world.files() == before


def test_node_key_of_a_host_never_admitted_is_node_invalid(world, node_key):
    before = world.files()
    unadmitted = world.credentials(uuid=SERVER_UUID).node_key

    assert world.post("distributed/read", {"node_key": unadmitted}).json() == NODE_INVALID
    assert world.files() == before


# --- resultados de log --------------------------------------------------------------------------


def hostile_events(node_uuid_upper: str) -> dict[str, list]:
    return {
        "host-identifier-ajeno": [
            snapshot_event("os_version", [OS_VERSION_ROW], host=SERVER_UUID.upper())
        ],
        "host-identifier-roto": [snapshot_event("os_version", [OS_VERSION_ROW], host="no-uuid")],
        "consulta-desconocida": [snapshot_event("drop_table_users", [OS_VERSION_ROW])],
        "consulta-con-sql": [snapshot_event("SELECT * FROM users", [OS_VERSION_ROW])],
        "demasiadas-columnas": [snapshot_event("os_version", [{f"c{i}": "v" for i in range(65)}])],
        "valor-enorme": [snapshot_event("os_version", [{"name": "x" * 4097}])],
        "valor-no-texto": [snapshot_event("os_version", [{"name": 7}])],
        "demasiadas-filas": [snapshot_event("os_version", [{}] * 100_001)],
        "snapshot-no-lista": [snapshot_event("os_version", {"name": "macOS"})],
        "evento-no-objeto": ["os_version", 7, None, [1]],
    }


@pytest.mark.parametrize("case", list(hostile_events("")))
def test_hostile_events_are_ignored_and_write_nothing(world, node_key, case):
    before = world.files()

    response = send_log(world, node_key, hostile_events("")[case])

    assert_ignored(world, response, before)


@pytest.mark.parametrize(
    "delta", [timedelta(minutes=6), timedelta(days=1), timedelta(days=365 * 30)]
)
def test_events_from_the_future_are_ignored(world, node_key, delta):
    before = world.files()
    event = snapshot_event("os_version", [OS_VERSION_ROW], unix=world.clock() + delta)

    assert_ignored(world, send_log(world, node_key, [event]), before)


@pytest.mark.parametrize(
    "unix_time",
    [
        pytest.param(
            -1,
            marks=pytest.mark.xfail(
                strict=True,
                reason="producto: un unixTime anterior a 1970 se acepta y se guarda como "
                "EV-19691231-*; debería ignorarse",
            ),
        ),
        2**70,
        "1700000000",
        1.5,
        True,
        None,
    ],
)
def test_events_with_absurd_timestamps_are_ignored(world, node_key, unix_time):
    before = world.files()
    event = {**snapshot_event("os_version", [OS_VERSION_ROW]), "unixTime": unix_time}

    assert_ignored(world, send_log(world, node_key, [event]), before)


def test_a_hostile_event_does_not_poison_the_valid_ones_in_the_batch(world, node_key):
    events = [
        snapshot_event("os_version", [OS_VERSION_ROW], host=SERVER_UUID.upper()),
        snapshot_event("os_version", [OS_VERSION_ROW]),
    ]

    assert send_log(world, node_key, events).status_code == 200
    (manifest,) = manifests(world)
    assert manifest.asset_id == LAPTOP


# --- consultas en vivo ---------------------------------------------------------------------------


def queue_for(world, *assets: str):
    return world.state.enqueue(
        "os_version", assets, world.clock() + timedelta(hours=1), "alex", now=world.clock()
    )


def answer(world, key, run_id, rows=(OS_VERSION_ROW,), status=0):
    return world.post(
        "distributed/write",
        {"node_key": key, "queries": {run_id: list(rows)}, "statuses": {run_id: status}},
    )


def test_run_id_delivered_to_another_host_cannot_be_answered(world, node_key):
    server_key = world.enroll(SERVER, uuid=SERVER_UUID, hostname=SERVER_HOST)
    run = queue_for(world, SERVER)
    assert list(world.post("distributed/read", {"node_key": server_key}).json()["queries"]) == [
        run.run_id
    ]
    before = world.files()

    response = answer(world, node_key, run.run_id)

    assert_ignored(world, response, before)
    (host,) = world.state.show_run(run.run_id).hosts
    assert host.answer is None


@pytest.mark.parametrize(
    "run_id", ["live-0000000000000000", "live-../../etc/passwd", "../x", "", "live-" + "f" * 4096]
)
def test_unknown_or_traversal_run_ids_are_ignored(world, node_key, run_id):
    before = world.files()

    assert_ignored(world, answer(world, node_key, run_id), before)


def test_run_not_yet_delivered_cannot_be_answered(world, node_key):
    run = queue_for(world, LAPTOP)
    before = world.files()

    assert_ignored(world, answer(world, node_key, run.run_id), before)


def test_replayed_distributed_write_is_stored_once(world, node_key):
    run = queue_for(world, LAPTOP)
    world.post("distributed/read", {"node_key": node_key})
    assert answer(world, node_key, run.run_id).status_code == 200
    (first,) = manifests(world)
    after_first = world.files()
    world.clock.advance(minutes=3)

    forged_rows = [{**OS_VERSION_ROW, "version": "99.0"}]
    replay = answer(world, node_key, run.run_id, rows=forged_rows)

    assert replay.status_code == 200
    assert [m.id for m in manifests(world)] == [first.id]
    stored = {k: v for k, v in world.files().items() if "agent/hosts" not in k}
    assert stored == {k: v for k, v in after_first.items() if "agent/hosts" not in k}


def test_hostile_answer_rows_become_an_error_without_stored_rows(world, node_key):
    run = queue_for(world, LAPTOP)
    world.post("distributed/read", {"node_key": node_key})

    answer(world, node_key, run.run_id, rows=[{f"c{i}": "v" for i in range(65)}])

    (manifest,) = manifests(world)
    artifact = json.loads(world.workspace.read_bytes(manifest.artifact.key))
    assert artifact["collection_status"] == "ERROR"
    assert artifact["data"]["queries"]["os_version"]["rows"] == []


@pytest.mark.parametrize("status", ["0", -1, 2**70, 1.5, None, True, [0]])
def test_hostile_status_never_reports_success_with_rows(world, node_key, status):
    run = queue_for(world, LAPTOP)
    world.post("distributed/read", {"node_key": node_key})

    response = answer(world, node_key, run.run_id, status=status)

    assert response.status_code == 200
    (manifest,) = manifests(world)
    artifact = json.loads(world.workspace.read_bytes(manifest.artifact.key))
    assert artifact["collection_status"] == "ERROR"
    assert artifact["data"]["queries"]["os_version"]["rows"] == []
