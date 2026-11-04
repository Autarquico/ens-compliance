"""Común a los clientes de CLI del plan contratado (plan pre-audit, «Cambios r2», punto 14).

Subprocess sin shell, payload por stdin, directorio de trabajo temporal y vacío, y timeout. Ni
stdout ni stderr se guardan ni aparecen en errores: solo se devuelve la respuesta extraída.

Estas CLIs usan la sesión del usuario (su plan contratado), nunca una clave de API: el subprocess
recibe un entorno mínimo (`PASSED_VARS`, `LC_*` y lo que pida cada cliente) y, aunque una variable
de credencial o de proveedor alternativo entrase por esa lista, se quita (`STRIPPED_*`).
"""

import os
import re
import shutil
import subprocess
from collections.abc import Collection, Mapping
from pathlib import Path

from ens_evidence.preaudit.adapter import JudgmentConfig, ProviderError, ProviderErrorKind

VERSION_TIMEOUT_SECONDS = 15.0
_VERSION = re.compile(rb"\d+(?:\.\d+)+")
USER_SESSION = "sesión del usuario (plan contratado)"
PASSED_VARS = frozenset({"PATH", "HOME", "USER", "LOGNAME", "LANG", "TMPDIR"})
# La sesión del propio plan contratado (`claude setup-token`): nunca una clave de API.
SESSION_VARS = frozenset({"CLAUDE_CODE_OAUTH_TOKEN"})
STRIPPED_VARS = frozenset(
    {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "OPENAI_BASE_URL"}
)
STRIPPED_PREFIXES = ("CLAUDE_CODE_",)


def cli_destination(name: str, version: str) -> str:
    """Destino al que se liga el consentimiento de un proveedor de CLI."""
    return f"{name} {version}, {USER_SESSION}"


def session_env(
    extra: Collection[str] = (), env: Mapping[str, str] | None = None
) -> dict[str, str]:
    """El entorno mínimo del subprocess: lo que la CLI necesita para encontrar la sesión."""
    source = os.environ if env is None else env
    return {
        name: value
        for name, value in source.items()
        if name in SESSION_VARS
        or (
            (name in PASSED_VARS or name.startswith("LC_") or name in extra)
            and name not in STRIPPED_VARS
            and not name.startswith(STRIPPED_PREFIXES)
        )
    }


def find_cli(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise ProviderError(
            ProviderErrorKind.CLI_MISSING,
            f"no se encuentra la CLI `{name}` en el PATH; instálala e inicia sesión en este equipo",
        )
    return path


def cli_version(executable: str, extra_env: Collection[str] = ()) -> str:
    output = run_cli([executable, "--version"], b"", None, VERSION_TIMEOUT_SECONDS, extra_env)
    match = _VERSION.search(output)
    if match is None:
        raise ProviderError(ProviderErrorKind.RESPONSE, "no se pudo leer la versión de la CLI")
    return match.group().decode()


def run_cli(
    args: list[str],
    stdin: bytes,
    cwd: Path | None,
    timeout: float,
    extra_env: Collection[str] = (),
) -> bytes:
    try:
        completed = subprocess.run(  # noqa: S603 - argv fijo, sin shell
            args,
            input=stdin,
            cwd=cwd,
            env=session_env(extra_env),
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise ProviderError(ProviderErrorKind.TIMEOUT, "la CLI no respondió a tiempo") from None
    except OSError:
        raise ProviderError(ProviderErrorKind.TRANSPORT, "no se pudo ejecutar la CLI") from None
    if completed.returncode != 0:
        raise ProviderError(
            ProviderErrorKind.RESPONSE, f"la CLI terminó con error (código {completed.returncode})"
        )
    return completed.stdout


def reject_temperature(config: JudgmentConfig, cli: str) -> None:
    if config.temperature is not None:
        raise ProviderError(
            ProviderErrorKind.CONFIG, f"{cli} no permite fijar la temperatura; no la indiques"
        )
