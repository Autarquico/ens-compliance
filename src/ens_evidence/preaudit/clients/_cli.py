"""Común a los clientes de CLI del plan contratado (plan pre-audit, «Cambios r2», punto 14).

Subprocess sin shell, payload por stdin, directorio de trabajo temporal y vacío, y timeout. Ni
stdout ni stderr se guardan ni aparecen en errores: solo se devuelve la respuesta extraída.

Estas CLIs usan la sesión del usuario (su plan contratado), nunca una clave de API: el subprocess
recibe un entorno mínimo (`PASSED_VARS`, `LC_*` y las variables de sesión que declare cada
cliente, solo las suyas) y, aunque una variable de credencial o de proveedor alternativo entrase
por esa lista, se quita (`STRIPPED_*`).

El ejecutable se resuelve una vez (ruta real y sha256) y es el que se lanza: el destino al que se
liga el consentimiento lo identifica, así que otro binario en el PATH cambia el destino.
"""

import hashlib
import os
import re
import shutil
import subprocess
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from pathlib import Path

from ens_evidence.preaudit.adapter import ProviderError, ProviderErrorKind

VERSION_TIMEOUT_SECONDS = 15.0
_VERSION = re.compile(rb"\d+(?:\.\d+)+")
USER_SESSION = "sesión del usuario (plan contratado)"
PASSED_VARS = frozenset({"PATH", "HOME", "USER", "LOGNAME", "LANG", "TMPDIR"})
STRIPPED_VARS = frozenset(
    {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "OPENAI_BASE_URL"}
)
STRIPPED_PREFIXES = ("CLAUDE_CODE_",)


@dataclass(frozen=True)
class Executable:
    path: str
    """Ruta real (sin enlaces simbólicos): la que se lanza."""
    sha256: str


def describe_cli(name: str, version: str, executable: Executable, notice: str) -> str:
    """Destino al que se liga el consentimiento de un proveedor de CLI: la CLI, su ejecutable
    concreto y lo que el aislamiento no garantiza (`notice`)."""
    return (
        f"{name} {version}, {USER_SESSION}; ejecutable {executable.path} "
        f"(sha256 {executable.sha256}); {notice}"
    )


def session_env(
    session: Collection[str] = (), env: Mapping[str, str] | None = None
) -> dict[str, str]:
    """El entorno mínimo del subprocess: lo común y las variables de sesión de esa CLI."""
    source = os.environ if env is None else env
    return {
        name: value
        for name, value in source.items()
        if name in session
        or (
            (name in PASSED_VARS or name.startswith("LC_"))
            and name not in STRIPPED_VARS
            and not name.startswith(STRIPPED_PREFIXES)
        )
    }


def find_cli(name: str) -> Executable:
    found = shutil.which(name)
    if found is None:
        raise ProviderError(
            ProviderErrorKind.CLI_MISSING,
            f"no se encuentra la CLI `{name}` en el PATH; instálala e inicia sesión en este equipo",
        )
    path = os.path.realpath(found)
    try:
        with open(path, "rb") as binary:
            digest = hashlib.file_digest(binary, "sha256").hexdigest()
    except OSError:
        raise ProviderError(
            ProviderErrorKind.CLI_MISSING, f"no se puede leer la CLI `{name}`"
        ) from None
    return Executable(path, digest)


def cli_version(executable: Executable, session: Collection[str] = ()) -> str:
    output = run_cli([executable.path, "--version"], b"", None, VERSION_TIMEOUT_SECONDS, session)
    match = _VERSION.search(output)
    if match is None:
        raise ProviderError(ProviderErrorKind.RESPONSE, "no se pudo leer la versión de la CLI")
    return match.group().decode()


def run_cli(
    args: list[str],
    stdin: bytes,
    cwd: Path | None,
    timeout: float,
    session: Collection[str] = (),
) -> bytes:
    try:
        completed = subprocess.run(  # noqa: S603 - argv fijo, sin shell
            args,
            input=stdin,
            cwd=cwd,
            env=session_env(session),
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
