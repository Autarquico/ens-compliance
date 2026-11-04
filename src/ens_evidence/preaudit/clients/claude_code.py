"""Proveedor `claude-code`: la CLI `claude` con la sesión del usuario (sin `--bare`, que no lee la
sesión OAuth). Flags verificados en `claude --help` 2.1.317; la salida de `--output-format json`
con `--json-schema` trae el resultado en `structured_output`, ausente si no lo hubo
(code.claude.com/docs/en/headless, consultado el 2026-11-04).
"""

import json
import tempfile
from pathlib import Path
from typing import Any

from ens_evidence.preaudit.adapter import JudgmentConfig, ProviderError, ProviderErrorKind
from ens_evidence.preaudit.clients._cli import (
    cli_destination,
    cli_version,
    find_cli,
    reject_temperature,
    run_cli,
)

CLI = "claude"
NAME = "claude-code"


class ClaudeCodeProvider:
    requires_shell_warning = False

    def __init__(self) -> None:
        self._executable = find_cli(CLI)
        self.version = cli_version(self._executable)

    def __repr__(self) -> str:
        return f"ClaudeCodeProvider({self.destination!r})"

    @property
    def destination(self) -> str:
        return cli_destination(NAME, self.version)

    def judge(
        self, system_prompt: str, payload: bytes, schema: dict[str, Any], config: JudgmentConfig
    ) -> str:
        reject_temperature(config, NAME)
        args = [
            self._executable,
            "-p",
            "--output-format", "json",
            "--json-schema", json.dumps(schema),
            "--system-prompt", system_prompt,
            "--tools", "",
            "--strict-mcp-config",
            "--no-session-persistence",
            "--model", config.model,
        ]  # fmt: skip
        with tempfile.TemporaryDirectory(prefix="ens-preaudit-") as workdir:
            stdout = run_cli(args, payload, Path(workdir), config.timeout)
        return _structured_output(stdout)


def _structured_output(stdout: bytes) -> str:
    try:
        reply = json.loads(stdout)
    except ValueError:
        raise ProviderError(ProviderErrorKind.RESPONSE, "salida de la CLI ilegible") from None
    if not isinstance(reply, dict) or reply.get("is_error") is True:
        raise ProviderError(ProviderErrorKind.RESPONSE, "la CLI informó de un error")
    output = reply.get("structured_output")
    if not isinstance(output, dict):
        raise ProviderError(ProviderErrorKind.RESPONSE, "la CLI no devolvió los hallazgos")
    return json.dumps(output, ensure_ascii=False)
