"""Ejecución de restic limitada a dos comandos de solo lectura (regla 9 de CLAUDE.md)."""

import shutil
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal

from ens_evidence.backup.environment import BackupSecrets, subprocess_env
from ens_evidence.backup.restic import READ_ONLY, TAG

CAT_CONFIG = (*READ_ONLY, "cat", "config")
LIST_SNAPSHOTS = (*READ_ONLY, "snapshots", "--json", "--tag", TAG)
LIST_ALL_SNAPSHOTS = (*READ_ONLY, "snapshots", "--json")  # solo el conector `backups`
ALLOWED = frozenset({CAT_CONFIG, LIST_SNAPSHOTS})
TIMEOUT_SECONDS = 300

Unavailable = Literal["not_installed", "os_error", "timeout", "not_text"]


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str


class ResticUnavailable(Exception):
    """restic no pudo ejecutarse; `kind` es el único diagnóstico (nunca salida de restic)."""

    def __init__(self, kind: Unavailable) -> None:
        super().__init__(kind)
        self.kind = kind


Executor = Callable[[tuple[str, ...]], CommandResult]


class ResticProcess:
    """Sin shell, entorno construido de cero, sin entrada estándar y sin guardar stderr."""

    def __init__(
        self,
        repository: str,
        secrets: BackupSecrets,
        *,
        base_env: Mapping[str, str] | None = None,
        region: str | None = None,
        allowed: frozenset[tuple[str, ...]] = ALLOWED,
    ) -> None:
        self._allowed = allowed
        self._repository = repository
        self._env = subprocess_env(secrets, base_env, region)

    def __call__(self, args: tuple[str, ...]) -> CommandResult:
        if args not in self._allowed:
            raise ValueError("comando de restic no permitido en la recogida")
        executable = shutil.which("restic", path=self._env["PATH"])
        if executable is None:
            raise ResticUnavailable("not_installed")
        try:
            result = subprocess.run(  # noqa: S603 - lista de argumentos, sin shell
                [executable, "--repo", self._repository, *args],
                env=self._env,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
                shell=False,
                timeout=TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            raise ResticUnavailable("timeout") from None
        except OSError:
            raise ResticUnavailable("os_error") from None
        except ValueError:
            raise ResticUnavailable("not_text") from None
        return CommandResult(result.returncode, result.stdout)
