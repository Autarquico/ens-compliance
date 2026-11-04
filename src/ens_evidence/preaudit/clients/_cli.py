"""Común a los clientes de CLI del plan contratado (plan pre-audit, «Cambios r2», punto 14).

Subprocess sin shell, payload por stdin, directorio de trabajo temporal y vacío, y timeout. Ni
stdout ni stderr se guardan ni aparecen en errores: solo se devuelve la respuesta extraída.
"""

import re
import shutil
import subprocess
from pathlib import Path

from ens_evidence.preaudit.adapter import JudgmentConfig, ProviderError, ProviderErrorKind

VERSION_TIMEOUT_SECONDS = 15.0
_VERSION = re.compile(rb"\d+(?:\.\d+)+")


def find_cli(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise ProviderError(
            ProviderErrorKind.CLI_MISSING,
            f"no se encuentra la CLI `{name}` en el PATH; instálala e inicia sesión en este equipo",
        )
    return path


def cli_version(executable: str) -> str:
    output = run_cli([executable, "--version"], b"", None, VERSION_TIMEOUT_SECONDS)
    match = _VERSION.search(output)
    if match is None:
        raise ProviderError(ProviderErrorKind.RESPONSE, "no se pudo leer la versión de la CLI")
    return match.group().decode()


def run_cli(args: list[str], stdin: bytes, cwd: Path | None, timeout: float) -> bytes:
    try:
        completed = subprocess.run(  # noqa: S603 - argv fijo, sin shell
            args, input=stdin, cwd=cwd, capture_output=True, timeout=timeout, check=False
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
