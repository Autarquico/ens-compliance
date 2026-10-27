"""Secretos de la copia y entorno aislado del subproceso restic."""

import os
from collections.abc import Mapping
from dataclasses import dataclass

from ens_evidence.backup.errors import BackupError

PASSWORD_VAR = "ENS_BACKUP_RESTIC_PASSWORD"
ACCESS_KEY_VAR = "ENS_BACKUP_AWS_ACCESS_KEY_ID"
SECRET_KEY_VAR = "ENS_BACKUP_AWS_SECRET_ACCESS_KEY"
_WORKSPACE_ACCESS_KEY_VAR = "AWS_ACCESS_KEY_ID"
_WORKSPACE_SECRET_KEY_VAR = "AWS_SECRET_ACCESS_KEY"
_DEFAULT_PATH = "/usr/local/bin:/usr/bin:/bin"


@dataclass(frozen=True)
class BackupSecrets:
    password: str
    access_key_id: str | None = None
    secret_access_key: str | None = None

    def values(self) -> list[str]:
        return [v for v in (self.password, self.access_key_id, self.secret_access_key) if v]


def load_secrets(repository: str, env: Mapping[str, str] | None = None) -> BackupSecrets:
    """Lee los secretos de `ENS_BACKUP_*`; rechaza los que comparte con el workspace."""
    env = os.environ if env is None else env
    password = env.get(PASSWORD_VAR)
    if not password:
        raise BackupError(f"falta la variable de entorno {PASSWORD_VAR}")
    access_key, secret_key = env.get(ACCESS_KEY_VAR) or None, env.get(SECRET_KEY_VAR) or None
    if (access_key is None) != (secret_key is None):
        raise BackupError(f"{ACCESS_KEY_VAR} y {SECRET_KEY_VAR} se indican juntas o ninguna")
    if repository.startswith("s3:") and access_key is None:
        raise BackupError(f"un repositorio s3 necesita {ACCESS_KEY_VAR} y {SECRET_KEY_VAR}")
    shared = [
        (ACCESS_KEY_VAR, access_key, env.get(_WORKSPACE_ACCESS_KEY_VAR)),
        (SECRET_KEY_VAR, secret_key, env.get(_WORKSPACE_SECRET_KEY_VAR)),
    ]
    for name, value, workspace_value in shared:
        if value is not None and value == workspace_value:
            raise BackupError(
                f"{name} coincide con la credencial del workspace; "
                "la copia debe usar credenciales distintas"
            )
    return BackupSecrets(password, access_key, secret_key)


def subprocess_env(
    secrets: BackupSecrets, base: Mapping[str, str] | None = None, region: str | None = None
) -> dict[str, str]:
    """Entorno construido de cero: solo los secretos de la copia, PATH y HOME."""
    base = os.environ if base is None else base
    env = {"RESTIC_PASSWORD": secrets.password, "PATH": base.get("PATH") or _DEFAULT_PATH}
    if base.get("HOME"):
        env["HOME"] = base["HOME"]
    if secrets.access_key_id and secrets.secret_access_key:
        env["AWS_ACCESS_KEY_ID"] = secrets.access_key_id
        env["AWS_SECRET_ACCESS_KEY"] = secrets.secret_access_key
    if region:
        env["AWS_DEFAULT_REGION"] = region
    return env


def redact(text: str, secrets: BackupSecrets, limit: int = 500) -> str:
    for value in sorted(secrets.values(), key=len, reverse=True):
        text = text.replace(value, "***")
    text = text.strip()
    return text if len(text) <= limit else "…" + text[-limit:]
