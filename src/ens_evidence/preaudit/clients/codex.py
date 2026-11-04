"""Proveedor `codex`: `codex exec` con la sesión del usuario. Flags verificados en
`codex exec --help` 0.160.1.

Herramienta de shell: `--disable shell_tool` la apaga (`codex features list` la muestra en
`false`), pero `unified_exec` sigue en `true` aunque se desactive con `--disable` o con
`-c features.unified_exec=false`. Como no se puede quitar toda ejecución de comandos y
`--sandbox read-only` no impide leer el sistema de ficheros, el destino exige aviso explícito en
el consentimiento (`requires_shell_warning`).

Las instrucciones del pack van como argumento PROMPT y el payload por stdin: `codex exec` lo
añade como bloque `<stdin>`. El esquema y la salida van en un directorio temporal distinto del
de trabajo, que queda vacío.
"""

import json
import tempfile
from pathlib import Path
from typing import Any

from ens_evidence.preaudit.adapter import JudgmentConfig, ProviderError, ProviderErrorKind
from ens_evidence.preaudit.clients._cli import cli_version, find_cli, reject_temperature, run_cli

CLI = "codex"
NAME = "codex"
# Funciones que dan acceso fuera del mensaje; todas aceptan `--disable` en 0.160.1.
DISABLED_FEATURES = (
    "shell_tool",
    "apps",
    "browser_use",
    "computer_use",
    "in_app_browser",
    "plugins",
    "code_mode_host",
    "hooks",
)


class CodexProvider:
    requires_shell_warning = True

    def __init__(self) -> None:
        self._executable = find_cli(CLI)
        self.version = cli_version(self._executable)

    def __repr__(self) -> str:
        return f"CodexProvider({self.destination!r})"

    @property
    def destination(self) -> str:
        return f"{NAME} {self.version}"

    def judge(
        self, system_prompt: str, payload: bytes, schema: dict[str, Any], config: JudgmentConfig
    ) -> str:
        reject_temperature(config, NAME)
        with (
            tempfile.TemporaryDirectory(prefix="ens-preaudit-") as workdir,
            tempfile.TemporaryDirectory(prefix="ens-preaudit-io-") as io_dir,
        ):
            schema_file = Path(io_dir) / "response.schema.json"
            output_file = Path(io_dir) / "last-message.json"
            schema_file.write_text(json.dumps(schema, ensure_ascii=False), encoding="utf-8")
            args = [
                self._executable,
                "exec",
                "--sandbox", "read-only",
                "--ephemeral",
                "--skip-git-repo-check",
                *(arg for feature in DISABLED_FEATURES for arg in ("--disable", feature)),
                "--output-schema", str(schema_file),
                "-o", str(output_file),
                "-m", config.model,
                "--",
                system_prompt,
            ]  # fmt: skip
            run_cli(args, payload, Path(workdir), config.timeout)
            try:
                return output_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                raise ProviderError(
                    ProviderErrorKind.RESPONSE, "la CLI no devolvió los hallazgos"
                ) from None
