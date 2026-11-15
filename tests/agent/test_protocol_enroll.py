import json
from datetime import timedelta

import pytest

from ens_evidence.agent.node_key import issue_enroll_token, issue_node_key, verify_node_key

from .conftest import SECRETS
from .protocol_support import (
    ENDPOINTS,
    LAPTOP,
    LAPTOP_UUID,
    SERVER,
    SERVER_HOST,
    SERVER_UUID,
    STRANGER_UUID,
    request_for,
    with_,
)

NODE_INVALID = {"node_invalid": True}


def flip_last(token: str) -> str:
    return token[:-1] + ("A" if token[-1] != "A" else "B")


def garbage(world):
    return world.credentials(node_key="x", enroll_token="x")


def bad_mac(world):
    c = world.credentials()
    return with_(c, node_key=flip_last(c.node_key), enroll_token=flip_last(c.enroll_token))


def wrong_generation(world):
    return world.credentials(generation=2)


def revoked(world):
    c = world.credentials()
    world.revoke()
    return c


def readmitted_with_stale_credentials(world):
    c = world.credentials()
    world.revoke()
    world.admit()
    return c


def never_admitted(world):
    return world.credentials(uuid=SERVER_UUID, hostname=SERVER_HOST)


def not_in_inventory(world):
    return world.credentials(uuid=STRANGER_UUID)


def other_secrets(world):
    c = world.credentials()
    expires = world.clock() + timedelta(hours=1)
    other = type(SECRETS)(enroll_key=b"E" * 32, node_key_secret=b"N" * 32)
    return with_(
        c,
        node_key=issue_node_key(other, c.uuid, 1),
        enroll_token=issue_enroll_token(other, c.uuid, 1, expires),
    )


INVALID = [
    garbage,
    bad_mac,
    wrong_generation,
    revoked,
    readmitted_with_stale_credentials,
    never_admitted,
    not_in_inventory,
    other_secrets,
]


@pytest.mark.parametrize("endpoint", ENDPOINTS)
@pytest.mark.parametrize("case", INVALID, ids=lambda case: case.__name__)
def test_every_invalid_credential_gets_the_same_answer_and_writes_nothing(world, endpoint, case):
    world.admit()
    credentials = case(world)
    before = world.files()

    response = world.post(endpoint, request_for(endpoint, credentials))

    assert response.status_code == 200
    assert response.json() == NODE_INVALID
    assert world.files() == before


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_requests_without_credentials_get_node_invalid(world, endpoint):
    world.admit()
    before = world.files()
    for body in ({}, {"host_identifier": LAPTOP_UUID}, {"node_key": 7}, [1], "texto", None):
        response = world.client.post(
            f"/agent/{endpoint}",
            content=json.dumps(body),
            headers={"content-type": "application/json"},
        )
        assert (response.status_code, response.json()) == (200, NODE_INVALID)
    assert world.files() == before


def test_valid_enroll_returns_a_node_key_for_the_current_generation(world):
    world.admit()

    response = world.post("enroll", request_for("enroll", world.credentials()))

    assert response.status_code == 200
    identity = verify_node_key(SECRETS, response.json()["node_key"])
    assert (identity.host_identifier, identity.generation) == (LAPTOP_UUID, 1)
    assert world.state.host(LAPTOP).platform == "darwin"


def test_enroll_after_revocation_and_new_admission_uses_the_new_generation(world):
    world.admit()
    world.revoke()
    world.admit()
    credentials = world.credentials(generation=2)

    node_key = world.post("enroll", request_for("enroll", credentials)).json()["node_key"]

    assert verify_node_key(SECRETS, node_key).generation == 2
    assert world.post("config", {"node_key": node_key}).json()["node_invalid"] is False


def test_expired_enroll_token_writes_nothing(world):
    world.admit(hours=24)
    credentials = world.credentials()
    world.clock.advance(hours=24, seconds=1)
    before = world.files()

    response = world.post("enroll", request_for("enroll", credentials))

    assert response.json() == NODE_INVALID
    assert world.files() == before


@pytest.mark.parametrize(
    "changes",
    [
        {"hostname": "otro.taller-norte.example"},
        {"host_identifier": SERVER_UUID},
        {"host_identifier": "not-a-uuid"},
        {"uuid": SERVER_UUID},
        {"uuid": "6f1c2d3e-4a5b-4c6d-8e9f-0a1b2c3d4e5f\n; DROP"},
    ],
    ids=["hostname", "host_identifier_of_other_host", "host_identifier_not_uuid", "uuid", "junk"],
)
def test_identity_mismatch_in_host_details_is_rejected(world, changes):
    world.admit()
    world.admit(SERVER)
    credentials = with_(world.credentials(), **changes)
    before = world.files()

    response = world.post("enroll", request_for("enroll", credentials))

    assert response.json() == NODE_INVALID
    assert world.files() == before


def test_enroll_token_of_one_host_does_not_enroll_another(world):
    world.admit()
    world.admit(SERVER)
    stolen = world.credentials().enroll_token
    server = world.credentials(uuid=SERVER_UUID, hostname=SERVER_HOST)

    response = world.post("enroll", request_for("enroll", with_(server, enroll_token=stolen)))

    assert response.json() == NODE_INVALID


def test_hostname_comparison_ignores_case(world):
    world.admit()
    credentials = with_(world.credentials(), hostname="LAP-ANA.Taller-Norte.EXAMPLE")

    response = world.post("enroll", request_for("enroll", credentials))

    assert "node_key" in response.json()


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_unreadable_or_oversized_bodies_use_http_errors(world, endpoint):
    small = endpoint in ("enroll", "config", "distributed/read")
    limit = 64 * 1024 if small else 10 * 1024 * 1024
    url = f"/agent/{endpoint}"
    json_type = {"content-type": "application/json"}

    assert world.client.post(url, content=b"{no json", headers=json_type).status_code == 400
    assert (
        world.client.post(url, content=b"{}", headers={"content-type": "text/plain"}).status_code
        == 415
    )
    big = b'{"x":"' + b"a" * (limit + 1) + b'"}'
    response = world.client.post(url, content=big, headers=json_type)
    assert response.status_code == 413
    assert "aaaa" not in response.text


def test_only_post_is_routed(world):
    assert world.client.get("/agent/enroll").status_code == 405
