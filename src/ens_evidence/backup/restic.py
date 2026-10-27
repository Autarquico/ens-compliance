"""Invocación de restic: sin shell, argumentos en lista y entorno explícito."""

import json
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ens_evidence.backup.environment import BackupSecrets, redact, subprocess_env
from ens_evidence.backup.errors import BackupError

TAG = "ens-evidence"
LATEST = "latest"
_REPOSITORY_MISSING = 10


class Restic:
    def __init__(
        self,
        repository: str,
        secrets: BackupSecrets,
        *,
        base_env: Mapping[str, str] | None = None,
        region: str | None = None,
    ) -> None:
        self._repository = repository
        self._secrets = secrets
        self._env = subprocess_env(secrets, base_env, region)

    def init(self) -> bool:
        """Crea el repositorio; devuelve False, sin tocarlo, si ya existe."""
        if self._exists():
            return False
        self._run(["init"])
        return True

    def _exists(self) -> bool:
        result = self._execute(["cat", "config"])
        if result.returncode == 0:
            return True
        if result.returncode == _REPOSITORY_MISSING:
            return False
        raise self._failure(result)

    def backup(self, directory: Path) -> str:
        """Hace snapshot de `directory` y devuelve su id."""
        output = self._run(["backup", "--json", "--tag", TAG, "."], cwd=directory)
        for line in reversed(output.splitlines()):
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if isinstance(message, dict) and message.get("message_type") == "summary":
                snapshot_id = message.get("snapshot_id")
                if isinstance(snapshot_id, str) and snapshot_id:
                    return snapshot_id
        raise BackupError("restic terminó sin indicar el id del snapshot; no se da por completada")

    def resolve_snapshot(self, snapshot: str) -> dict[str, Any]:
        """Devuelve el snapshot (`id`, `paths`, `time`) para un id o para `latest`."""
        if snapshot == LATEST:
            found = self._snapshots(["--tag", TAG])
        else:
            found = self._snapshots([snapshot])
        if not found:
            raise BackupError("no hay ningún snapshot que coincida")
        return max(found, key=lambda item: str(item.get("time", "")))

    def restore(self, snapshot: dict[str, Any], target: Path) -> None:
        """Restaura el contenido del snapshot (la raíz del workspace) en `target`."""
        self._run(["restore", str(snapshot["id"]), "--target", str(target)])

    def _snapshots(self, extra: list[str]) -> list[dict[str, Any]]:
        parsed = json.loads(self._run(["snapshots", "--json", *extra]) or "[]")
        if not isinstance(parsed, list) or not all(isinstance(item, dict) for item in parsed):
            raise BackupError("restic devolvió un listado de snapshots no válido")
        return parsed

    def _run(self, args: list[str], cwd: Path | None = None) -> str:
        result = self._execute(args, cwd)
        if result.returncode != 0:
            raise self._failure(result)
        return result.stdout

    def _failure(self, result: subprocess.CompletedProcess[str]) -> BackupError:
        detail = redact(result.stderr or result.stdout, self._secrets)
        return BackupError(f"restic falló (código {result.returncode}): {detail}")

    def _execute(
        self, args: list[str], cwd: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        executable = shutil.which("restic", path=self._env["PATH"])
        if executable is None:
            raise BackupError("restic no está instalado o no está en el PATH")
        try:
            result = subprocess.run(  # noqa: S603 - lista de argumentos, sin shell
                [executable, "--repo", self._repository, *args],
                env=self._env,
                cwd=cwd,
                capture_output=True,
                text=True,
                check=False,
                shell=False,
            )
        except OSError as exc:
            raise BackupError(f"no se pudo ejecutar restic: {type(exc).__name__}") from None
        except ValueError:
            raise BackupError("restic devolvió una salida que no es texto") from None
        return result
