"""Refresco del JWKS de Access con transporte HTTPX falso: nunca hay red."""

import base64
import json
import logging
import stat
import threading

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from ens_evidence.scheduling import access_jwks
from ens_evidence.scheduling.access_jwks import (
    JwksRefreshConfig,
    JwksRefreshLoop,
    config_from_env,
    refresh_once,
)

TEAM = "acme.cloudflareaccess.com"
OLD = b'{"keys": [{"kid": "old"}]}'


def b64(number: int) -> str:
    raw = number.to_bytes((number.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def jwk(kid: str = "k1", bits: int = 2048) -> dict:
    numbers = rsa.generate_private_key(65537, bits).public_key().public_numbers()
    return {
        "kty": "RSA",
        "kid": kid,
        "use": "sig",
        "alg": "RS256",
        "n": b64(numbers.n),
        "e": b64(numbers.e),
    }


@pytest.fixture(scope="module")
def good_key() -> dict:
    return jwk()


@pytest.fixture
def config(tmp_path) -> JwksRefreshConfig:
    out = tmp_path / "jwks" / "access-jwks.json"
    out.parent.mkdir()
    out.write_bytes(OLD)
    return JwksRefreshConfig(team_domain=TEAM, output=out)


def client_for(handler):
    return lambda: httpx.Client(transport=httpx.MockTransport(handler))


def serving(body: bytes, status_code: int = 200, headers=None):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status_code, content=body, headers=headers)

    handler.seen = seen
    return handler


def test_success_publishes_keys_with_readable_mode(config, good_key):
    handler = serving(json.dumps({"keys": [good_key, jwk("k2")], "public_cert": {"x": 1}}).encode())

    assert refresh_once(config, client_for(handler)) == "ok"

    published = json.loads(config.output.read_text())
    assert [k["kid"] for k in published["keys"]] == ["k1", "k2"]
    assert str(handler.seen[0].url) == f"https://{TEAM}/cdn-cgi/access/certs"
    assert stat.S_IMODE(config.output.stat().st_mode) == 0o644
    assert [p.name for p in config.output.parent.iterdir()] == [config.output.name]


def test_published_file_has_final_mode_before_replace(config, good_key, monkeypatch):
    modes: list[int] = []
    real_replace = access_jwks.os.replace

    def spy(src, dst):
        modes.append(stat.S_IMODE(access_jwks.os.stat(src).st_mode))
        real_replace(src, dst)

    monkeypatch.setattr(access_jwks.os, "replace", spy)
    refresh_once(config, client_for(serving(json.dumps({"keys": [good_key]}).encode())))
    assert modes == [0o644]


def failing_cases(good_key):
    big_key = {**good_key, "pad": "x" * (70 * 1024)}
    return {
        "too_large": serving(json.dumps({"keys": [big_key]}).encode()),
        "redirect": serving(b"", 302, {"location": "https://evil.example/certs"}),
        "bad_status": serving(b"{}", 503),
        "bad_json": serving(b"not json"),
        "bad_keys": serving(json.dumps({"keys": [{**good_key, "n": "***"}]}).encode()),
        "bad_keys ": serving(json.dumps({"keys": []}).encode()),
        "duplicate_kid": serving(json.dumps({"keys": [good_key, good_key]}).encode()),
        "too_many_keys": serving(
            json.dumps({"keys": [{**good_key, "kid": str(i)} for i in range(21)]}).encode()
        ),
        "weak_key": serving(json.dumps({"keys": [jwk("small", 1024)]}).encode()),
    }


@pytest.mark.parametrize(
    "name",
    [
        "too_large",
        "redirect",
        "bad_status",
        "bad_json",
        "bad_keys",
        "bad_keys ",
        "duplicate_kid",
        "too_many_keys",
        "weak_key",
    ],
)
def test_failure_keeps_previous_file_and_logs_code(config, good_key, name, caplog):
    handler = failing_cases(good_key)[name]
    with caplog.at_level(logging.INFO):
        code = refresh_once(config, client_for(handler))

    assert code == name.strip()
    assert config.output.read_bytes() == OLD
    assert f"({code})" in caplog.text


def test_non_rsa_or_wrong_alg_key_is_rejected(config, good_key):
    for bad in ({**good_key, "kty": "EC"}, {**good_key, "alg": "RS512"}):
        assert (
            refresh_once(config, client_for(serving(json.dumps({"keys": [bad]}).encode())))
            == "bad_keys"
        )
    assert config.output.read_bytes() == OLD


def test_network_error_keeps_previous_file(config, caplog):
    def handler(request):
        raise httpx.ConnectError("secret-detail")

    with caplog.at_level(logging.INFO):
        assert refresh_once(config, client_for(handler)) == "network_error"

    assert config.output.read_bytes() == OLD
    assert "secret-detail" not in caplog.text


def test_timeout_is_reported(config):
    def handler(request):
        raise httpx.ReadTimeout("slow")

    assert refresh_once(config, client_for(handler)) == "timeout"
    assert config.output.read_bytes() == OLD


def test_write_failure_keeps_previous_file_and_leaves_no_temp(config, good_key, monkeypatch):
    def boom(src, dst):
        raise OSError("disk")

    monkeypatch.setattr(access_jwks.os, "replace", boom)
    code = refresh_once(config, client_for(serving(json.dumps({"keys": [good_key]}).encode())))

    assert code == "write_failed"
    assert config.output.read_bytes() == OLD
    assert [p.name for p in config.output.parent.iterdir()] == [config.output.name]


@pytest.mark.parametrize(
    "team",
    [
        "evil.example.com",
        "acme.cloudflareaccess.com.evil.example",
        "a.b.cloudflareaccess.com",
        "cloudflareaccess.com",
        "127.0.0.1",
        "acme.cloudflareaccess.com/path",
        "user@acme.cloudflareaccess.com",
    ],
)
def test_bad_host_is_not_configured(team, tmp_path):
    env = {access_jwks.TEAM_DOMAIN_ENV: team, access_jwks.OUTPUT_ENV: str(tmp_path / "j.json")}
    assert config_from_env(env) is None


def test_config_from_env_accepts_team_host_and_requires_absolute_path(tmp_path):
    ok = {
        access_jwks.TEAM_DOMAIN_ENV: "Acme.CloudflareAccess.com",
        access_jwks.OUTPUT_ENV: str(tmp_path / "j.json"),
    }
    assert config_from_env(ok) == JwksRefreshConfig(TEAM, tmp_path / "j.json")
    assert config_from_env({**ok, access_jwks.OUTPUT_ENV: "relative.json"}) is None


def test_without_config_job_is_not_registered(caplog):
    with caplog.at_level(logging.INFO):
        assert config_from_env({}) is None
    assert "no configurado" in caplog.text


def test_loop_refreshes_at_startup_and_retries_after_failure(config, good_key, monkeypatch):
    waits: list[float] = []
    stop = threading.Event()
    results = iter(["bad_status", "ok"])

    def fake_wait(delay):
        waits.append(delay)
        return len(waits) > 3

    monkeypatch.setattr(stop, "wait", fake_wait)
    monkeypatch.setattr(access_jwks, "refresh_once", lambda *_: next(results, "ok"))

    JwksRefreshLoop(config, stop).run()

    assert waits == [0.0, 15 * 60.0, 6 * 3600.0, 6 * 3600.0]
