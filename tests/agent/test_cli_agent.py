from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from typer.testing import CliRunner

from ens_evidence.agent.node_key import verify_enroll_token
from ens_evidence.agent.registry import load_registry
from ens_evidence.agent.secrets import AgentSecrets
from ens_evidence.cli import app
from ens_evidence.workspace.cycle_lock import CycleLock
from ens_evidence.workspace.settings import load_settings

from .protocol_support import LAPTOP, LAPTOP_UUID, NOW, OS_VERSION_ROW, SERVER

runner = CliRunner()
ENROLL = "enroll-" + "k" * 40
NODE = "node-" + "s" * 40
ENV = {"ENS_AGENT_ENROLL_KEY": ENROLL, "ENS_AGENT_NODE_KEY_SECRET": NODE}


@pytest.fixture
def settings(world, tmp_path, monkeypatch) -> Path:
    path = tmp_path / "settings.yaml"
    path.write_text(
        yaml.safe_dump(
            {"workspace": {"backend": "local", "local": {"path": str(world.workspace_root)}}}
        )
    )
    monkeypatch.setenv("ENS_STATE_DIR", str(world.state_dir))
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr("ens_evidence.cli.clock", world.clock)
    return path


def ens(settings: Path, *args: str):
    return runner.invoke(app, ["--settings", str(settings), "agent", *args])


def held_by_another(settings: Path) -> CycleLock:
    return CycleLock.for_settings(load_settings(settings, allow_inside_product=True))


def serve_config(settings, *args):
    with patch("uvicorn.run") as run:
        result = ens(settings, "serve", *args)
    return result, run


def test_serve_runs_uvicorn_with_hardened_limits(settings):
    result, run = serve_config(settings, "--host", "0.0.0.0", "--port", "8000")
    assert result.exit_code == 0, result.output
    (application,), kwargs = run.call_args
    assert kwargs == {
        "host": "0.0.0.0",
        "port": 8000,
        "server_header": False,
        "access_log": False,
        "proxy_headers": False,
        "limit_concurrency": 64,
        "timeout_keep_alive": 5,
        "timeout_graceful_shutdown": 10,
        "h11_max_incomplete_event_size": 16384,
        "log_config": kwargs["log_config"],
    }
    assert kwargs["log_config"]["filters"]
    from starlette.testclient import TestClient

    assert TestClient(application).get("/healthz").json() == {"status": "ok"}
    assert ENROLL not in result.output and NODE not in result.output


def test_serve_defaults_to_loopback(settings):
    _, run = serve_config(settings)
    assert run.call_args.kwargs["host"] == "127.0.0.1"
    assert run.call_args.kwargs["port"] == 8000


@pytest.mark.parametrize(
    "env",
    [
        {"ENS_AGENT_ENROLL_KEY": None},
        {"ENS_AGENT_ENROLL_KEY": "corta"},
        {"ENS_AGENT_NODE_KEY_SECRET": ENROLL},
    ],
)
def test_serve_rejects_bad_secrets_without_printing_them(settings, monkeypatch, env):
    for name, value in env.items():
        monkeypatch.delenv(name) if value is None else monkeypatch.setenv(name, value)
    result, run = serve_config(settings)
    assert result.exit_code == 2
    run.assert_not_called()
    for secret in (ENROLL, NODE, "corta"):
        assert secret not in result.output


def test_serve_requires_a_local_workspace(tmp_path, settings):
    s3 = tmp_path / "s3.yaml"
    s3.write_text(yaml.safe_dump({"workspace": {"backend": "s3", "s3": {"bucket": "b"}}}))
    result, run = serve_config(s3)
    assert result.exit_code == 2
    assert "local" in result.output
    run.assert_not_called()


def test_admit_records_and_prints_only_the_token_on_stdout(settings, world):
    result = runner.invoke(
        app,
        ["--settings", str(settings), "agent", "admit", "--asset", LAPTOP, "--by", "alex"],
    )
    assert result.exit_code == 0, result.output
    token = result.stdout.strip()
    assert token.startswith("e1.") and "\n" not in token
    assert token not in result.stderr and ENROLL not in result.output
    assert "--enroll_secret_path" in result.stderr
    record = load_registry(world.workspace).agents[LAPTOP]
    secrets = AgentSecrets(enroll_key=ENROLL.encode(), node_key_secret=NODE.encode())
    assert verify_enroll_token(secrets, token, LAPTOP_UUID, record.generation, NOW)
    assert not verify_enroll_token(
        secrets, token, LAPTOP_UUID, record.generation, NOW + timedelta(hours=25)
    )


def test_admit_unknown_asset_or_busy_lock_changes_nothing(settings, world):
    unknown = ens(settings, "admit", "--asset", "fantasma", "--by", "alex")
    assert unknown.exit_code == 2
    with held_by_another(settings):
        busy = ens(settings, "admit", "--asset", LAPTOP, "--by", "alex")
    assert busy.exit_code == 2
    assert load_registry(world.workspace).agents == {}


def test_revoke_bumps_generation_and_hosts_shows_state(settings, world):
    ens(settings, "admit", "--asset", LAPTOP, "--by", "alex")
    assert "admitido (generación 1)" in ens(settings, "hosts").stdout
    result = ens(settings, "revoke", "--asset", LAPTOP, "--by", "alex", "--reason", "robado")
    assert result.exit_code == 0, result.output
    listing = ens(settings, "hosts").stdout
    assert f"{LAPTOP}\t" in listing and "revocado (generación 2)" in listing
    assert "sin alta" in listing and "nunca" in listing
    with held_by_another(settings):
        assert (
            ens(settings, "revoke", "--asset", LAPTOP, "--by", "a", "--reason", "x").exit_code == 2
        )


def test_query_flow_ends_with_the_evidence_id(settings, world):
    node_key = world.enroll()
    run = ens(settings, "query", "run", "--asset", LAPTOP, "--query", "os_version", "--by", "alex")
    assert run.exit_code == 0, run.output
    run_id = run.stdout.split(":")[0]
    assert "pendiente de entrega" in ens(settings, "query", "show", run_id).stdout

    world.post("distributed/read", {"node_key": node_key})
    assert "entregada, sin respuesta" in ens(settings, "query", "show", run_id).stdout
    world.post(
        "distributed/write",
        {"node_key": node_key, "queries": {run_id: [OS_VERSION_ROW]}, "statuses": {run_id: 0}},
    )
    shown = ens(settings, "query", "show", run_id).stdout
    answer = world.state.show_run(run_id).hosts[0].answer
    assert answer.evidence_id and f"evidencia: {answer.evidence_id}" in shown
    assert run_id in ens(settings, "query", "list").stdout


@pytest.mark.parametrize(
    "args",
    [
        ["--query", "os_version"],
        ["--query", "os_version", "--asset", LAPTOP, "--all"],
        ["--query", "no_existe", "--asset", LAPTOP],
        ["--query", "os_version", "--asset", "fantasma"],
        ["--query", "os_version", "--asset", LAPTOP, "--expires", "ayer"],
    ],
)
def test_query_run_rejects_invalid_targets_and_queries(settings, world, args):
    result = ens(settings, "query", "run", "--by", "alex", *args)
    assert result.exit_code == 2
    assert world.state.list_runs() == []


def test_query_run_all_targets_every_enrolled_host(settings, world):
    result = ens(
        settings,
        "query",
        "run",
        "--all",
        "--query",
        "os_version",
        "--by",
        "alex",
        "--expires",
        "2d",
    )
    assert result.exit_code == 0, result.output
    (run,) = world.state.list_runs()
    assert set(run.targets) == {LAPTOP, SERVER}
    assert run.expires_at == NOW + timedelta(days=2)


def test_query_show_unknown_run(settings):
    assert ens(settings, "query", "show", "live-0123456789abcdef").exit_code == 1


def test_query_list_hides_expired_runs_unless_all(settings, world):
    ens(settings, "query", "run", "--asset", LAPTOP, "--query", "os_version", "--by", "a")
    (run,) = world.state.list_runs()
    expired = run.expires_at - timedelta(hours=48)
    world.state.enqueue("os_version", [SERVER], NOW + timedelta(hours=1), "a", now=NOW)
    old = world.state.enqueue(
        "os_version", [SERVER], expired + timedelta(hours=1), "a", now=expired
    )

    assert old.run_id not in ens(settings, "query", "list").stdout
    assert old.run_id in ens(settings, "query", "list", "--all").stdout
    assert run.run_id in ens(settings, "query", "list").stdout


def test_query_run_caps_expiry_like_admission(settings, world):
    too_long = ens(
        settings,
        "query",
        "run",
        "--asset",
        LAPTOP,
        "--query",
        "os_version",
        "--by",
        "a",
        "--expires",
        "8d",
    )
    assert too_long.exit_code == 2 and world.state.list_runs() == []
    week = ens(
        settings,
        "query",
        "run",
        "--asset",
        LAPTOP,
        "--query",
        "os_version",
        "--by",
        "a",
        "--expires",
        "7d",
    )
    assert week.exit_code == 0, week.output
