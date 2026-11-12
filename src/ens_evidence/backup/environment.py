"""Secretos de la copia y entorno aislado del subproceso restic."""

import os
from collections.abc import Mapping
from dataclasses import dataclass

from ens_evidence.backup.errors import BackupError

DEFAULT_PREFIX = "ENS_BACKUP_"
PASSWORD_VAR = f"{DEFAULT_PREFIX}RESTIC_PASSWORD"
ACCESS_KEY_VAR = f"{DEFAULT_PREFIX}AWS_ACCESS_KEY_ID"
SECRET_KEY_VAR = f"{DEFAULT_PREFIX}AWS_SECRET_ACCESS_KEY"
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


def load_secrets(
    repository: str, env: Mapping[str, str] | None = None, prefix: str = DEFAULT_PREFIX
) -> BackupSecrets:
    """Lee los secretos de `<prefix>*` (por defecto `ENS_BACKUP_*`); rechaza los que comparte
    con el workspace."""
    env = os.environ if env is None else env
    password_var = f"{prefix}RESTIC_PASSWORD"
    access_var = f"{prefix}AWS_ACCESS_KEY_ID"
    secret_var = f"{prefix}AWS_SECRET_ACCESS_KEY"
    password = env.get(password_var)
    if not password:
        raise BackupError(f"falta la variable de entorno {password_var}")
    access_key, secret_key = env.get(access_var) or None, env.get(secret_var) or None
    if (access_key is None) != (secret_key is None):
        raise BackupError(f"{access_var} y {secret_var} se indican juntas o ninguna")
    if repository.startswith("s3:") and access_key is None:
        raise BackupError(f"un repositorio s3 necesita {access_var} y {secret_var}")
    shared = [
        (access_var, access_key, env.get(_WORKSPACE_ACCESS_KEY_VAR)),
        (secret_var, secret_key, env.get(_WORKSPACE_SECRET_KEY_VAR)),
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
