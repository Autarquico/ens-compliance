"""Ni los logs ni la salida de error de la CLI contienen tokens, node_key, secretos ni cuerpos."""

import logging

import pytest
import yaml
from starlette.testclient import TestClient
from typer.testing import CliRunner

from ens_evidence.cli import app

from .conftest import SECRETS
from .protocol_support import (
    LAPTOP,
    LAPTOP_HOST,
    LAPTOP_UUID,
    OS_VERSION_ROW,
    enroll_body,
    snapshot_event,
)
from .test_cli_agent import ENROLL, NODE, settings  # noqa: F401

CANARY = "CANARIO-DE-CUERPO-7f3a"
runner = CliRunner()


def tainted_body(body: dict) -> dict:
    return {**body, "canary": CANARY}


def leaks(text: str, *secrets: str) -> list[str]:
    return [s for s in (CANARY, *secrets) if s and s in text]


def test_every_endpoint_logs_without_tokens_keys_or_bodies(world, caplog):
    caplog.set_level(logging.DEBUG)
    world.admit()
    credentials = world.credentials()
    enrolled = world.post("enroll", tainted_body(enroll_body(credentials))).json()
    node_key = enrolled["node_key"]
    events = [snapshot_event("os_version", [{**OS_VERSION_ROW, "name": CANARY}])]

    calls = [
        ("config", {"node_key": node_key}),
        ("log", {"node_key": node_key, "log_type": "result", "data": events}),
        ("log", {"node_key": node_key, "log_type": "status", "data": [{"message": CANARY}]}),
        ("distributed/read", {"node_key": node_key}),
        ("distributed/write", {"node_key": node_key, "queries": {"live-0123456789abcdef": []}}),
        ("config", {"node_key": node_key[:-3] + "AAA", "canary": CANARY}),
        ("enroll", tainted_body(enroll_body(credentials) | {"enroll_secret": "e1.1.bad"})),
    ]
    for endpoint, body in calls:
        world.post(endpoint, tainted_body(body))
    world.client.post(
        "/agent/log",
        content=b'{"node_key": "' + CANARY.encode() + b'", ',
        headers={"content-type": "application/json"},
    )

    assert caplog.records
    secrets = (
        node_key,
        credentials.enroll_token,
        "e1.1.bad",
        SECRETS.enroll_key.decode(),
        SECRETS.node_key_secret.decode(),
        LAPTOP_UUID,
        LAPTOP_HOST,
    )
    assert leaks(caplog.text, *secrets) == []
    assert all(CANARY not in str(r.args) and CANARY not in r.getMessage() for r in caplog.records)


def test_unexpected_errors_log_only_the_exception_type(world, caplog, monkeypatch):
    caplog.set_level(logging.DEBUG)
    node_key = world.enroll()

    def explode():
        raise RuntimeError(f"fallo con {CANARY}")

    monkeypatch.setattr("ens_evidence.agent.protocol.build_config", explode)
    client = TestClient(world.client.app, raise_server_exceptions=False)

    response = client.post("/agent/config", json=tainted_body({"node_key": node_key}))

    assert response.status_code == 500
    assert CANARY not in response.text
    assert "RuntimeError" in caplog.text
    assert leaks(caplog.text, node_key) == []


@pytest.mark.parametrize(
    "args",
    [
        ["admit", "--asset", "no-existe", "--by", "alex"],
        ["admit", "--asset", LAPTOP, "--by", "alex", "--hours", "9999"],
        ["revoke", "--asset", "no-existe", "--by", "alex", "--reason", "x"],
        ["serve", "--port", "0"],
    ],
    ids=["admit-desconocido", "admit-horas", "revoke-desconocido", "serve-puerto"],
)
def test_cli_failures_do_not_print_secrets(settings, caplog, args):  # noqa: F811
    caplog.set_level(logging.DEBUG)

    result = runner.invoke(app, ["--settings", str(settings), "agent", *args])

    assert result.exit_code != 0
    assert leaks(result.output + caplog.text, ENROLL, NODE) == []


@pytest.mark.parametrize(
    "broken",
    [
        {"ENS_AGENT_ENROLL_KEY": None},
        {"ENS_AGENT_ENROLL_KEY": "corta-" + "c" * 4},
        {"ENS_AGENT_NODE_KEY_SECRET": ENROLL},
    ],
    ids=["falta", "corto", "iguales"],
)
@pytest.mark.parametrize("command", [["serve"], ["admit", "--asset", LAPTOP, "--by", "alex"]])
def test_cli_secret_errors_name_the_variable_but_never_the_value(
    settings,  # noqa: F811
    caplog,
    monkeypatch,
    broken,
    command,
):
    caplog.set_level(logging.DEBUG)
    for name, value in broken.items():
        monkeypatch.delenv(name) if value is None else monkeypatch.setenv(name, value)

    result = runner.invoke(app, ["--settings", str(settings), "agent", *command])

    assert result.exit_code == 2
    seen = result.output + caplog.text
    assert leaks(seen, ENROLL, NODE, *(v for v in broken.values() if v)) == []
    assert "ENS_AGENT_" in result.output


def test_cli_s3_workspace_error_does_not_print_secrets(tmp_path, settings, caplog):  # noqa: F811
    caplog.set_level(logging.DEBUG)
    s3 = tmp_path / "s3.yaml"
    s3.write_text(yaml.safe_dump({"workspace": {"backend": "s3", "s3": {"bucket": "b"}}}))

    result = runner.invoke(app, ["--settings", str(s3), "agent", "serve"])

    assert result.exit_code == 2
    assert leaks(result.output + caplog.text, ENROLL, NODE) == []


def test_cli_admit_prints_the_token_only_on_stdout_and_never_logs_it(settings, caplog):  # noqa: F811
    caplog.set_level(logging.DEBUG)

    result = runner.invoke(
        app, ["--settings", str(settings), "agent", "admit", "--asset", LAPTOP, "--by", "alex"]
    )

    token = result.stdout.strip()
    assert result.exit_code == 0 and token.startswith("e1.")
    assert token not in result.stderr
    assert leaks(caplog.text, token, ENROLL, NODE) == []
