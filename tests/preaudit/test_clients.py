"""Clientes de la capa de juicio con respuestas grabadas y CLIs falsas. Nunca hay red real."""

import json
import stat
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from ens_evidence.models.preaudit import LayerStatus
from ens_evidence.preaudit.adapter import (
    SCHEMA_ERROR,
    JudgmentConfig,
    ProviderError,
    ProviderErrorKind,
    parse_response,
    run_judgment,
)
from ens_evidence.preaudit.clients._cli import session_env
from ens_evidence.preaudit.clients.anthropic import AnthropicProvider
from ens_evidence.preaudit.clients.claude_code import ClaudeCodeProvider
from ens_evidence.preaudit.clients.codex import CodexProvider
from ens_evidence.preaudit.clients.openai import OpenAIProvider
from ens_evidence.preaudit.pack import load_pack

PACK = load_pack()
SECRET = "sk-test-NUNCA-DEBE-SALIR-0123456789abcdef"
CONFIG = JudgmentConfig(model="modelo-elegido", timeout=5)
PAYLOAD = json.dumps({"dossier": {"measures": [], "documents": [], "evidence": []}}).encode()
VALID = {"findings": []}
INVALID = {"findings": [{"criterion": "x", "verdict": "FAVORABLE"}]}


def anthropic_reply(tool_input: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "msg_01",
        "type": "message",
        "role": "assistant",
        "model": "modelo-elegido",
        "stop_reason": "tool_use",
        "content": [
            {"type": "tool_use", "id": "toolu_01", "name": "record_findings", "input": tool_input}
        ],
    }


def openai_reply(content: str | None, *, refusal: str | None = None, finish: str = "stop"):
    message = {"role": "assistant", "content": content, "refusal": refusal}
    return {
        "id": "chatcmpl-1",
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
    }


Handler = Callable[[httpx.Request], httpx.Response]


def recording(*responses: httpx.Response) -> tuple[list[httpx.Request], Handler]:
    seen: list[httpx.Request] = []
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return queue.pop(0)

    return seen, handler


def anthropic(handler: Handler, sleeps: list[float] | None = None, **kwargs: Any):
    return AnthropicProvider(
        env={"ANTHROPIC_API_KEY": SECRET},
        transport=httpx.MockTransport(handler),
        sleep=(sleeps if sleeps is not None else []).append,
        **kwargs,
    )


def openai(handler: Handler, sleeps: list[float] | None = None, **kwargs: Any):
    return OpenAIProvider(
        env={"OPENAI_API_KEY": SECRET},
        transport=httpx.MockTransport(handler),
        sleep=(sleeps if sleeps is not None else []).append,
        **kwargs,
    )


def judge(provider: Any, config: JudgmentConfig = CONFIG) -> str:
    return provider.judge(PACK.system_prompt, PAYLOAD, PACK.response_schema, config)


# --- Anthropic ---------------------------------------------------------------------------------


def test_anthropic_valid_response() -> None:
    seen, handler = recording(httpx.Response(200, json=anthropic_reply(VALID)))

    raw = judge(anthropic(handler))

    assert parse_response(raw) is not None
    [request] = seen
    assert str(request.url) == "https://api.anthropic.com/v1/messages"
    assert request.headers["x-api-key"] == SECRET
    assert request.headers["anthropic-version"] == "2023-06-01"
    body = json.loads(request.content)
    assert body["model"] == "modelo-elegido"
    assert body["system"] == PACK.system_prompt
    assert body["messages"] == [{"role": "user", "content": PAYLOAD.decode()}]
    assert body["tools"][0]["input_schema"] == PACK.response_schema
    assert body["tool_choice"] == {"type": "tool", "name": body["tools"][0]["name"]}
    assert "temperature" not in body


def test_anthropic_sends_temperature_only_when_configured() -> None:
    seen, handler = recording(httpx.Response(200, json=anthropic_reply(VALID)))

    judge(anthropic(handler), JudgmentConfig(model="m", temperature=0.0))

    assert json.loads(seen[0].content)["temperature"] == 0.0


def test_anthropic_schema_violation_leaves_layer_in_error() -> None:
    _, handler = recording(httpx.Response(200, json=anthropic_reply(INVALID)))

    outcome = run_judgment(anthropic(handler), PACK, PAYLOAD, CONFIG)

    assert outcome.status is LayerStatus.ERROR
    assert outcome.findings == () and outcome.error == SCHEMA_ERROR


@pytest.mark.parametrize(
    "reply",
    [
        {"content": [{"type": "text", "text": "{}"}], "stop_reason": "end_turn"},
        {**anthropic_reply(VALID), "stop_reason": "max_tokens"},
        {"content": "no es una lista"},
    ],
)
def test_anthropic_without_tool_result_is_an_error(reply: dict[str, Any]) -> None:
    _, handler = recording(httpx.Response(200, json=reply))

    with pytest.raises(ProviderError) as raised:
        judge(anthropic(handler))
    assert raised.value.kind is ProviderErrorKind.RESPONSE


def test_429_is_retried_with_wait() -> None:
    sleeps: list[float] = []
    seen, handler = recording(
        httpx.Response(429, headers={"retry-after": "3"}),
        httpx.Response(200, json=anthropic_reply(VALID)),
    )

    judge(anthropic(handler, sleeps))

    assert len(seen) == 2 and sleeps == [3.0]


def test_5xx_is_retried_at_most_twice() -> None:
    sleeps: list[float] = []
    seen, handler = recording(*(httpx.Response(503) for _ in range(3)))

    with pytest.raises(ProviderError) as raised:
        judge(anthropic(handler, sleeps))

    assert len(seen) == 3 and sleeps == [1.0, 2.0]
    assert raised.value.kind is ProviderErrorKind.HTTP and raised.value.http_status == 503


def test_other_4xx_is_not_retried() -> None:
    seen, handler = recording(httpx.Response(400, json={"error": {"message": PAYLOAD.decode()}}))

    with pytest.raises(ProviderError) as raised:
        judge(anthropic(handler))

    assert len(seen) == 1
    assert PAYLOAD.decode() not in str(raised.value)


def test_redirect_is_rejected_and_not_followed() -> None:
    seen, handler = recording(
        httpx.Response(307, headers={"location": "https://otro.example/v1/messages"})
    )

    with pytest.raises(ProviderError) as raised:
        judge(anthropic(handler))

    assert raised.value.kind is ProviderErrorKind.REDIRECT
    assert [r.url.host for r in seen] == ["api.anthropic.com"]


@pytest.mark.parametrize(
    "base_url",
    [
        "http://api.anthropic.com",
        "ftp://api.anthropic.com",
        "https://user:pass@api.anthropic.com",
        "https://api.anthropic.com?x=1",
        "https://",
    ],
)
def test_non_https_base_url_is_rejected(base_url: str) -> None:
    with pytest.raises(ProviderError) as raised:
        anthropic(lambda r: httpx.Response(200), base_url=base_url)
    assert raised.value.kind is ProviderErrorKind.CONFIG


def test_base_url_is_normalized_and_used() -> None:
    seen, handler = recording(httpx.Response(200, json=anthropic_reply(VALID)))
    provider = anthropic(handler, base_url="https://Proxy.Example:8443/anthropic/")

    judge(provider)

    assert provider.destination == "https://proxy.example:8443/anthropic"
    assert str(seen[0].url) == "https://proxy.example:8443/anthropic/v1/messages"


def test_missing_key_is_a_config_error() -> None:
    with pytest.raises(ProviderError) as raised:
        AnthropicProvider(env={})
    assert raised.value.kind is ProviderErrorKind.CONFIG
    assert "ANTHROPIC_API_KEY" in raised.value.message


def _raise(error: Exception) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error

    return handler


@pytest.mark.parametrize("factory", [anthropic, openai])
@pytest.mark.parametrize(
    "handler",
    [
        lambda r: httpx.Response(401, json={"error": SECRET}),
        _raise(httpx.ConnectError(f"fallo con {SECRET}")),
        _raise(httpx.ReadTimeout(f"timeout con {SECRET}")),
    ],
)
def test_key_never_appears_in_exceptions_or_repr(factory: Any, handler: Handler) -> None:
    provider = factory(handler)

    with pytest.raises(ProviderError) as raised:
        judge(provider)

    error = raised.value
    assert error.__cause__ is None
    assert error.__context__ is None or error.__suppress_context__
    assert SECRET not in f"{error} {error!r} {error.message} {provider!r} {provider._poster!r}"


# --- OpenAI ------------------------------------------------------------------------------------


def test_openai_valid_response() -> None:
    seen, handler = recording(httpx.Response(200, json=openai_reply(json.dumps(VALID))))

    raw = judge(openai(handler))

    assert parse_response(raw) is not None
    [request] = seen
    assert str(request.url) == "https://api.openai.com/v1/chat/completions"
    assert request.headers["authorization"] == f"Bearer {SECRET}"
    body = json.loads(request.content)
    assert body["model"] == "modelo-elegido"
    assert body["messages"][0] == {"role": "system", "content": PACK.system_prompt}
    assert body["messages"][1] == {"role": "user", "content": PAYLOAD.decode()}
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["response_format"]["json_schema"]["schema"] == PACK.response_schema


def test_openai_schema_violation_leaves_layer_in_error() -> None:
    _, handler = recording(httpx.Response(200, json=openai_reply(json.dumps(INVALID))))

    outcome = run_judgment(openai(handler), PACK, PAYLOAD, CONFIG)

    assert outcome.status is LayerStatus.ERROR and outcome.findings == ()


@pytest.mark.parametrize(
    "reply",
    [
        openai_reply(None, refusal="no puedo"),
        openai_reply('{"findings": [', finish="length"),
        {"choices": []},
    ],
)
def test_openai_refusal_or_truncation_is_an_error(reply: dict[str, Any]) -> None:
    _, handler = recording(httpx.Response(200, json=reply))

    with pytest.raises(ProviderError) as raised:
        judge(openai(handler))
    assert raised.value.kind is ProviderErrorKind.RESPONSE


def test_openai_429_is_retried() -> None:
    sleeps: list[float] = []
    seen, handler = recording(
        httpx.Response(429), httpx.Response(200, json=openai_reply(json.dumps(VALID)))
    )

    judge(openai(handler, sleeps))

    assert len(seen) == 2 and sleeps == [1.0]


def test_openai_rejects_http() -> None:
    with pytest.raises(ProviderError):
        openai(lambda r: httpx.Response(200), base_url="http://api.openai.com")


# --- CLIs del plan contratado ------------------------------------------------------------------

FAKE_CLI = """
import json, os, sys, time
name = os.path.basename(sys.argv[0])
args = sys.argv[1:]
if args == ["--version"]:
    print({"claude": "2.1.317 (Claude Code)", "codex": "codex-cli 0.160.1"}[name])
    sys.exit(0)
# El subprocess recibe un entorno mínimo: el modo y el registro van junto al script.
here = os.path.dirname(os.path.abspath(sys.argv[0]))
mode_file = os.path.join(here, "mode")
mode = open(mode_file).read() if os.path.exists(mode_file) else "ok"
stdin = sys.stdin.buffer.read()
with open(os.path.join(here, "log.json"), "w") as log:
    record = {"args": args, "cwd_entries": os.listdir("."), "stdin": stdin.decode()}
    json.dump(record | {"env": dict(os.environ)}, log)
if mode == "sleep":
    time.sleep(30)
if mode == "fail":
    sys.stderr.write("stderr con datos privados")
    sys.exit(3)
result = {"findings": []} if mode == "ok" else {"findings": [{"verdict": "FAVORABLE"}]}
if name == "claude":
    reply = {"type": "result", "subtype": "success", "is_error": False, "result": "texto"}
    if mode != "no_structured":
        reply["structured_output"] = result
    print(json.dumps(reply))
else:
    out = args[args.index("-o") + 1]
    with open(out, "w") as f:
        json.dump(result, f)
    print("salida de agente que no se guarda")
"""


@pytest.fixture
def fake_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("claude", "codex"):
        script = bin_dir / name
        script.write_text(f"#!{sys.executable}\n{FAKE_CLI}")
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", str(bin_dir))
    return bin_dir / "log.json"


def logged(log: Path) -> dict[str, Any]:
    return json.loads(log.read_text())


def fake_mode(log: Path, mode: str) -> None:
    log.with_name("mode").write_text(mode)


def test_claude_code_valid_response(fake_cli: Path) -> None:
    provider = ClaudeCodeProvider()

    raw = judge(provider)

    assert parse_response(raw) is not None
    assert provider.destination == "claude-code 2.1.317, sesión del usuario (plan contratado)"
    assert provider.requires_shell_warning is False
    call = logged(fake_cli)
    args = call["args"]
    assert args[:3] == ["-p", "--output-format", "json"]
    assert json.loads(args[args.index("--json-schema") + 1]) == PACK.response_schema
    assert args[args.index("--system-prompt") + 1] == PACK.system_prompt
    assert args[args.index("--tools") + 1] == ""
    assert {"--strict-mcp-config", "--no-session-persistence"} <= set(args)
    assert args[args.index("--model") + 1] == "modelo-elegido"
    assert "--bare" not in args
    assert call["stdin"] == PAYLOAD.decode()
    assert call["cwd_entries"] == []


def test_codex_valid_response(fake_cli: Path) -> None:
    provider = CodexProvider()

    raw = judge(provider)

    assert parse_response(raw) is not None
    assert provider.destination == "codex 0.160.1, sesión del usuario (plan contratado)"
    assert provider.requires_shell_warning is True
    call = logged(fake_cli)
    args = call["args"]
    assert args[0] == "exec"
    assert args[args.index("--sandbox") + 1] == "read-only"
    assert {"--ephemeral", "--skip-git-repo-check"} <= set(args)
    assert args[args.index("--disable") + 1] == "shell_tool"
    assert args[args.index("-m") + 1] == "modelo-elegido"
    assert args[-2:] == ["--", PACK.system_prompt]
    assert call["stdin"] == PAYLOAD.decode()
    assert call["cwd_entries"] == []


@pytest.mark.parametrize("provider_class", [ClaudeCodeProvider, CodexProvider])
def test_cli_schema_violation_leaves_layer_in_error(
    provider_class: Any, fake_cli: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_mode(fake_cli, "invalid")

    outcome = run_judgment(provider_class(), PACK, PAYLOAD, CONFIG)

    assert outcome.status is LayerStatus.ERROR and outcome.findings == ()


def test_claude_code_without_structured_output_is_an_error(
    fake_cli: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_mode(fake_cli, "no_structured")

    with pytest.raises(ProviderError) as raised:
        judge(ClaudeCodeProvider())
    assert raised.value.kind is ProviderErrorKind.RESPONSE


@pytest.mark.parametrize("provider_class", [ClaudeCodeProvider, CodexProvider])
def test_cli_failure_does_not_leak_stderr(
    provider_class: Any, fake_cli: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_mode(fake_cli, "fail")

    with pytest.raises(ProviderError) as raised:
        judge(provider_class())
    assert "privados" not in str(raised.value) and "código 3" in str(raised.value)


@pytest.mark.parametrize("provider_class", [ClaudeCodeProvider, CodexProvider])
def test_cli_timeout(provider_class: Any, fake_cli: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_mode(fake_cli, "sleep")

    with pytest.raises(ProviderError) as raised:
        judge(provider_class(), JudgmentConfig(model="m", timeout=0.5))
    assert raised.value.kind is ProviderErrorKind.TIMEOUT


@pytest.mark.parametrize("provider_class", [ClaudeCodeProvider, CodexProvider])
def test_cli_missing_from_path(
    provider_class: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))

    with pytest.raises(ProviderError) as raised:
        provider_class()
    assert raised.value.kind is ProviderErrorKind.CLI_MISSING
    assert "PATH" in raised.value.message


@pytest.mark.parametrize("provider_class", [ClaudeCodeProvider, CodexProvider])
def test_cli_rejects_temperature(provider_class: Any, fake_cli: Path) -> None:
    with pytest.raises(ProviderError) as raised:
        judge(provider_class(), JudgmentConfig(model="m", temperature=0.2))
    assert raised.value.kind is ProviderErrorKind.CONFIG
    assert not fake_cli.exists()


API_CREDENTIALS = {
    "ANTHROPIC_API_KEY": "sk-ant-nunca",
    "ANTHROPIC_AUTH_TOKEN": "token-nunca",
    "ANTHROPIC_BASE_URL": "https://gateway.example.com",
    "CLAUDE_CODE_USE_BEDROCK": "1",
    "CLAUDE_CODE_USE_VERTEX": "1",
    "OPENAI_API_KEY": "sk-nunca",
    "OPENAI_BASE_URL": "https://gateway.example.com/v1",
    "AWS_SECRET_ACCESS_KEY": "nunca",
}
SESSION_ONLY = {"PATH", "HOME", "LANG", "LC_ALL", "TMPDIR"}


@pytest.mark.parametrize(
    ("provider_class", "allowed"),
    [(ClaudeCodeProvider, SESSION_ONLY), (CodexProvider, SESSION_ONLY | {"CODEX_HOME"})],
)
def test_cli_providers_use_the_user_session_and_never_an_api_key(
    provider_class: Any, allowed: set[str], fake_cli: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name, value in API_CREDENTIALS.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("LC_ALL", "es_ES.UTF-8")
    monkeypatch.setenv("CODEX_HOME", str(fake_cli.parent))

    provider = provider_class()
    judge(provider)

    env = logged(fake_cli)["env"]
    assert not set(API_CREDENTIALS) & set(env)
    assert {name for name in env if not name.startswith(("LC_", "__CF"))} <= allowed
    assert env["PATH"] == str(fake_cli.parent) and env["LC_ALL"] == "es_ES.UTF-8"
    assert ("CODEX_HOME" in env) is (provider_class is CodexProvider)
    assert provider.destination.endswith(", sesión del usuario (plan contratado)")


def test_the_session_env_strips_api_keys_even_if_listed_and_keeps_the_plan_session() -> None:
    env = {
        "PATH": "/bin",
        "CLAUDE_CODE_OAUTH_TOKEN": "x",
        "CLAUDE_CODE_USE_BEDROCK": "1",
        "OPENAI_API_KEY": "y",
        "LC_CTYPE": "C",
    }

    assert session_env(("CLAUDE_CODE_USE_BEDROCK", "OPENAI_API_KEY"), env) == {
        "PATH": "/bin",
        "CLAUDE_CODE_OAUTH_TOKEN": "x",
        "LC_CTYPE": "C",
    }
