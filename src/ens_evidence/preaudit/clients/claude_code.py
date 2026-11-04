"""Proveedor `claude-code`: la CLI `claude` con la sesión del usuario (sin `--bare`, que no lee la
sesión OAuth). Flags verificados en `claude --help` 2.1.317; la salida de `--output-format json`
con `--json-schema` trae el resultado en `structured_output`, ausente si no lo hubo
(code.claude.com/docs/en/headless, consultado el 2026-11-04).

Sin la configuración del usuario: `--restricted` ignora los ficheros de ajustes de usuario,
proyecto y local (su `env`, `apiKeyHelper`, hooks y modelo por defecto); `--safe-mode` apaga
CLAUDE.md, plugins, hooks, MCP y agentes propios sin tocar la autenticación; `--strict-mcp-config`
sin `--mcp-config` deja cero servidores MCP; `--settings` añade `disableAllHooks` y
`--disable-slash-commands` quita las skills. Los ajustes gestionados (managed settings) siguen
aplicando por diseño de la CLI: va en el aviso del destino (`NOTICE`).
"""

import json
import tempfile
from pathlib import Path
from typing import Any

from ens_evidence.preaudit.adapter import JudgmentConfig, ProviderError, ProviderErrorKind
from ens_evidence.preaudit.clients._cli import cli_version, describe_cli, find_cli, run_cli

CLI = "claude"
NAME = "claude-code"
# La sesión del propio plan contratado (`claude setup-token`): nunca una clave de API.
SESSION_ENV = ("CLAUDE_CODE_OAUTH_TOKEN",)
ISOLATION_SETTINGS = json.dumps({"disableAllHooks": True})
NOTICE = (
    "sin ajustes, hooks, MCP ni CLAUDE.md del usuario; los ajustes gestionados por la "
    "organización en este equipo siguen aplicando"
)


class ClaudeCodeProvider:
    requires_shell_warning = False

    def __init__(self) -> None:
        self._executable = find_cli(CLI)
        self.version = cli_version(self._executable, SESSION_ENV)

    def __repr__(self) -> str:
        return f"ClaudeCodeProvider({self.destination!r})"

    @property
    def destination(self) -> str:
        return describe_cli(NAME, self.version, self._executable, NOTICE)

    def judge(
        self, system_prompt: str, payload: bytes, schema: dict[str, Any], config: JudgmentConfig
    ) -> str:
        args = [
            self._executable.path,
            "-p",
            "--output-format", "json",
            "--json-schema", json.dumps(schema),
            "--system-prompt", system_prompt,
            "--tools", "",
            "--restricted",
            "--safe-mode",
            "--strict-mcp-config",
            "--settings", ISOLATION_SETTINGS,
            "--disable-slash-commands",
            "--no-session-persistence",
            "--model", config.model,
        ]  # fmt: skip
        with tempfile.TemporaryDirectory(prefix="ens-preaudit-") as workdir:
            stdout = run_cli(args, payload, Path(workdir), config.timeout, SESSION_ENV)
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
