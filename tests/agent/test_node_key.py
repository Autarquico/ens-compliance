import hmac
from datetime import UTC, datetime, timedelta

import pytest

from ens_evidence.agent import node_key
from ens_evidence.agent.node_key import (
    NodeIdentity,
    issue_enroll_token,
    issue_node_key,
    normalize_host_identifier,
    verify_enroll_token,
    verify_node_key,
)
from ens_evidence.agent.secrets import AgentSecrets

HID = "6f1c2d3e-4a5b-4c6d-8e9f-0a1b2c3d4e5f"
OTHER = "11111111-2222-4333-8444-555555555555"
NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
SECRETS = AgentSecrets(enroll_key=b"e" * 32, node_key_secret=b"n" * 32)
OTHER_SECRETS = AgentSecrets(enroll_key=b"E" * 32, node_key_secret=b"N" * 32)


def test_node_key_round_trip():
    key = issue_node_key(SECRETS, HID, 3)
    assert key.startswith("v1.")
    assert verify_node_key(SECRETS, key) == NodeIdentity(HID, 3)


def test_node_key_is_deterministic_and_case_insensitive_on_uuid():
    assert issue_node_key(SECRETS, HID.upper(), 1) == issue_node_key(SECRETS, HID, 1)


def test_tampered_mac_is_rejected():
    key = issue_node_key(SECRETS, HID, 1)
    flipped = key[:-1] + ("A" if key[-1] != "A" else "B")
    assert verify_node_key(SECRETS, flipped) is None


def test_changing_generation_without_resigning_is_rejected():
    head, gen, mac = issue_node_key(SECRETS, HID, 1).rsplit(".", 2)
    assert verify_node_key(SECRETS, f"{head}.2.{mac}") is None


def test_changing_host_with_the_same_mac_is_rejected():
    other = issue_node_key(SECRETS, OTHER, 1).split(".")
    mine = issue_node_key(SECRETS, HID, 1).split(".")
    assert verify_node_key(SECRETS, ".".join([mine[0], other[1], mine[2], mine[3]])) is None


def test_other_secret_is_rejected():
    assert verify_node_key(OTHER_SECRETS, issue_node_key(SECRETS, HID, 1)) is None


@pytest.mark.parametrize(
    "garbage",
    ["", "v1.", "v1...", "v2.a.1.b", "v1.!!!.1.!!!", "v1.a.1", "v1.a.-1.b", "x" * 600, "v1.é.1.é"],
)
def test_garbage_is_rejected_without_raising(garbage):
    assert verify_node_key(SECRETS, garbage) is None


@pytest.mark.parametrize(
    "raw", ["", "host", "../etc", HID + "x", "a" * 129, HID[:-1], HID.replace("-", "")]
)
def test_host_identifier_must_be_a_uuid(raw):
    assert normalize_host_identifier(raw) is None
    with pytest.raises(ValueError):
        issue_node_key(SECRETS, raw, 1)


def test_verification_uses_compare_digest(monkeypatch):
    calls = []
    real = hmac.compare_digest

    def spy(a, b):
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(node_key.hmac, "compare_digest", spy)
    verify_node_key(SECRETS, issue_node_key(SECRETS, HID, 1))
    verify_enroll_token(
        SECRETS, issue_enroll_token(SECRETS, HID, 1, NOW + timedelta(hours=1)), HID, 1, NOW
    )
    assert len(calls) == 2


def token(**overrides):
    args = {"host_identifier": HID, "generation": 1, "expires_at": NOW + timedelta(hours=24)}
    args |= overrides
    return issue_enroll_token(
        SECRETS, args["host_identifier"], args["generation"], args["expires_at"]
    )


def test_enroll_token_valid_for_its_host_and_generation():
    assert verify_enroll_token(SECRETS, token(), HID, 1, NOW)


def test_enroll_token_expired():
    assert not verify_enroll_token(SECRETS, token(), HID, 1, NOW + timedelta(hours=24))
    assert not verify_enroll_token(SECRETS, token(), HID, 1, NOW + timedelta(days=2))


def test_enroll_token_for_another_host_or_generation_is_rejected():
    assert not verify_enroll_token(SECRETS, token(), OTHER, 1, NOW)
    assert not verify_enroll_token(SECRETS, token(), HID, 2, NOW)


def test_enroll_token_extended_expiry_is_rejected():
    _, expiry, mac = token().split(".")
    forged = f"e1.{int(expiry) + 86400}.{mac}"
    assert not verify_enroll_token(SECRETS, forged, HID, 1, NOW)


def test_enroll_token_tampered_or_garbage():
    good = token()
    assert not verify_enroll_token(SECRETS, good[:-2] + "AA", HID, 1, NOW)
    assert not verify_enroll_token(OTHER_SECRETS, good, HID, 1, NOW)
    for bad in ["", "e1.", "e1.1.2.3", "e1.x.y", "v1.1.2", "é"]:
        assert not verify_enroll_token(SECRETS, bad, HID, 1, NOW)


def test_node_key_and_enroll_token_are_not_interchangeable():
    assert verify_node_key(SECRETS, token()) is None
