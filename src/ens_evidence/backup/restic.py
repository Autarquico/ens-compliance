"""Invocación de restic: sin shell, argumentos en lista y entorno explícito."""

import json
import re
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ens_evidence.backup.environment import BackupSecrets, redact, subprocess_env
from ens_evidence.backup.errors import BackupError

TAG = "ens-evidence"
LATEST = "latest"
_REPOSITORY_MISSING = 10
READ_ONLY = ("--no-lock", "--no-cache")
READ_TIMEOUT_SECONDS = 300
_REPOSITORY_ID = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class CheckOutcome:
    """Resumen estructurado de `restic check --json`; nunca la salida en bruto."""

    exit_code: int
    num_errors: int | None
    broken_packs: int | None


class Restic:
    def __init__(
        self,
        repository: str,
        secrets: BackupSecrets,
        *,
        base_env: Mapping[str, str] | None = None,
        region: str | None = None,
        read_timeout: float = READ_TIMEOUT_SECONDS,
    ) -> None:
        self._repository = repository
        self._secrets = secrets
        self._env = subprocess_env(secrets, base_env, region)
        self._read_timeout = read_timeout
        self._repository_id: str | None = None

    def init(self) -> bool:
        """Crea el repositorio; devuelve False, sin tocarlo, si ya existe."""
        if self._exists():
            return False
        self._run(["init"])
        return True

    def _exists(self) -> bool:
        result = self._execute(["cat", "config"], timeout=self._read_timeout)
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

    def repository_id(self) -> str:
        """Id del repositorio (`cat config`), leído una vez. Solo lectura: sin lock ni caché."""
        if self._repository_id is None:
            found = parse_repository_id(self._read([*READ_ONLY, "cat", "config"]))
            if found is None:
                raise BackupError("restic devolvió una configuración de repositorio sin id válido")
            self._repository_id = found
        return self._repository_id

    def check(self, read_data_subset: str | None = None, read_data: bool = False) -> CheckOutcome:
        """`restic check --json`. Toma el lock normal del repositorio: NO es de solo lectura.

        Un código distinto de 0 es un resultado (`exit_code`), no una excepción.
        """
        args = ["check", "--json"]
        if read_data_subset is not None:
            args += ["--read-data-subset", read_data_subset]
        elif read_data:
            args.append("--read-data")
        result = self._execute(args)
        summary = _check_summary(result.stdout)
        if summary is None:
            return CheckOutcome(result.returncode, None, None)
        broken = summary.get("broken_packs")
        return CheckOutcome(
            result.returncode,
            _count(summary.get("num_errors")),
            0 if broken is None else (len(broken) if isinstance(broken, list) else None),
        )

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
        return _parse_snapshots(self._run(["snapshots", "--json", *extra]))

    def _run(self, args: list[str], cwd: Path | None = None) -> str:
        result = self._execute(args, cwd)
        if result.returncode != 0:
            raise self._failure(result)
        return result.stdout

    def _read(self, args: list[str]) -> str:
        """Como `_run`, pero el error solo lleva el código de salida, nunca la salida de restic."""
        result = self._execute(args, timeout=self._read_timeout)
        if result.returncode != 0:
            raise BackupError(f"restic falló (código {result.returncode})")
        return result.stdout

    def _failure(self, result: subprocess.CompletedProcess[str]) -> BackupError:
        detail = redact(result.stderr or result.stdout, self._secrets)
        return BackupError(f"restic falló (código {result.returncode}): {detail}")

    def _execute(
        self, args: list[str], cwd: Path | None = None, timeout: float | None = None
    ) -> subprocess.CompletedProcess[str]:
        executable = shutil.which("restic", path=self._env["PATH"])
        if executable is None:
            raise BackupError("restic no está instalado o no está en el PATH")
        try:
            result = subprocess.run(  # noqa: S603 - lista de argumentos, sin shell
                [executable, "--repo", self._repository, *args],
                env=self._env,
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
                shell=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            raise BackupError(f"restic no respondió en {timeout:g} s") from None
        except OSError as exc:
            raise BackupError(f"no se pudo ejecutar restic: {type(exc).__name__}") from None
        except ValueError:
            raise BackupError("restic devolvió una salida que no es texto") from None
        return result


def parse_repository_id(output: str) -> str | None:
    """Id de la salida de `cat config`, o None si no es un id válido."""
    try:
        found = json.loads(output).get("id")
    except (ValueError, AttributeError):
        return None
    return found if isinstance(found, str) and _REPOSITORY_ID.fullmatch(found) else None


def _parse_snapshots(output: str) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(output or "[]")
    except ValueError:
        parsed = None
    if not isinstance(parsed, list) or not all(isinstance(item, dict) for item in parsed):
        raise BackupError("restic devolvió un listado de snapshots no válido")
    return parsed


def _check_summary(stdout: str) -> dict[str, Any] | None:
    for line in reversed(stdout.splitlines()):
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if isinstance(message, dict) and message.get("message_type") == "summary":
            return message
    return None


def _count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
