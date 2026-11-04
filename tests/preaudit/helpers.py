"""Mundos y proveedores falsos de la pre-auditoría: expedientes reales (`stages`), la API de
Anthropic con `httpx.MockTransport` y CLIs falsas en el `PATH`. Nunca hay red real."""

import json
import re
import stat
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import yaml
from assessment.dossier.stages import AFTER, ID, restore, run_at
from matrix.acceptance_helpers import import_manual_evidence, link

from ens_evidence.preaudit import service
from ens_evidence.preaudit.clients.anthropic import TOOL_NAME, AnthropicProvider

MODEL = "modelo-elegido"
MEASURE = "op.mon.1"
POLICY_DOCUMENT = "politica-seguridad"
CONTRADICTION = "Las cuentas de administración no tienen doble factor de autenticación."
SHA256 = re.compile(r"Huella del payload \(sha256\): ([0-9a-f]{64})")


def configure(world, **preaudit: Any) -> None:
    """Añade la sección `preaudit` a los ajustes del mundo."""
    settings = yaml.safe_load(world.settings.read_text())
    settings["preaudit"] = {"provider": "anthropic", "model": MODEL} | preaudit
    world.settings.write_text(yaml.safe_dump(settings))


def world_at(stages, tmp_path, monkeypatch):
    return restore(stages, "generated", tmp_path, monkeypatch)


def regenerate(world) -> str:
    result = run_at(world, "assessment", "dossier", "generate", at=AFTER)
    assert result.exit_code == 0, result.output
    return ID.search(result.output).group()


def with_evidence(world, tmp_path, content: str, *, document: bool = False) -> tuple[str, str]:
    """Una evidencia manual con `content` enlazada a `MEASURE` (y, si `document`, también la
    política) y un expediente nuevo que la incluye: (expediente, evidencia)."""
    world.at(AFTER - timedelta(minutes=5))
    evidence = import_manual_evidence(world.run, tmp_path, content=content)
    link(world.run, MEASURE, "evidence", evidence, "base")
    if document:
        link(world.run, MEASURE, "document", POLICY_DOCUMENT, "base")
    return regenerate(world), evidence


def prepared_sha(output: str) -> str:
    match = SHA256.search(output)
    assert match, output
    return match.group(1)


# --- proveedores falsos ------------------------------------------------------------------------


def literal_line(text: str) -> str:
    """Una línea del texto que se puede citar tal cual: con letras y sin marcas de redacción."""
    return next(
        line.strip()
        for line in text.splitlines()
        if len(line.strip()) > 10 and "[" not in line and not any(c.isdigit() for c in line)
    )


def traceable(payload: dict[str, Any], evidence: str) -> dict[str, Any]:
    """La respuesta de un modelo que ve la política contradicha por la evidencia."""
    content = payload["dossier"]
    policy = next(d for d in content["documents"] if d["ref"] == POLICY_DOCUMENT)
    return {
        "findings": [
            {
                "criterion": "coherencia-politica-evidencia",
                "measure": MEASURE,
                "proposal": "NC_MENOR",
                "documents": [{"ref": POLICY_DOCUMENT, "quote": literal_line(policy["text"])}],
                "evidence": [{"ref": evidence, "quote": CONTRADICTION}],
                "justifications": [],
                "reason": "La política exige controles que la evidencia muestra que no se aplican.",
            }
        ]
    }


class FakeAnthropic:
    """La Messages API grabada: devuelve `reply(payload)` como entrada de la herramienta."""

    def __init__(self, reply) -> None:
        self.reply = reply
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = json.loads(request.content)
        payload = json.loads(body["messages"][0]["content"])
        tool_input = self.reply(payload)
        return httpx.Response(
            200,
            json={
                "id": "msg_01",
                "type": "message",
                "role": "assistant",
                "model": MODEL,
                "stop_reason": "tool_use",
                "content": [
                    {"type": "tool_use", "id": "toolu_01", "name": TOOL_NAME, "input": tool_input}
                ],
            },
        )

    def install(self, monkeypatch) -> "FakeAnthropic":
        def client(provider, config):
            if provider.value != "anthropic":
                return original(provider, config)
            return AnthropicProvider(
                config.base_url or service.DEFAULT_BASE_URL[provider],
                env={"ANTHROPIC_API_KEY": "sk-ant-test"},
                transport=httpx.MockTransport(self.handler),
            )

        original = service.default_client
        monkeypatch.setattr(service, "default_client", client)
        return self


FAKE_CODEX = """
import json, os, sys
args = sys.argv[1:]
if args == ["--version"]:
    print("codex-cli 0.160.1")
    sys.exit(0)
sys.stdin.buffer.read()
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write("called\\n")
out = args[args.index("-o") + 1]
with open(out, "w") as f:
    json.dump({"findings": []}, f)
"""


def fake_codex(tmp_path: Path, monkeypatch) -> Path:
    """`codex` falso en el PATH; devuelve el fichero que anota cada envío."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "codex"
    script.write_text(f"#!{sys.executable}\n{FAKE_CODEX}")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    log = tmp_path / "codex.log"
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("FAKE_LOG", str(log))
    return log
