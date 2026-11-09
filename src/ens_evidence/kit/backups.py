"""Kit de copias: entradas validadas y paquete de instalación (tar.gz determinista).

Produce los ficheros del contrato C2 de `.claude/loop/kit-backups/plan.md`.
Nada se escribe en disco ni en el workspace: los secretos solo viven en los bytes devueltos.
El formato de `config.json` es el de Backrest v1.14.1 (`proto/v1/config.proto`).
"""

import gzip
import hashlib
import io
import json
import re
import secrets
import tarfile
from datetime import datetime
from pathlib import PurePosixPath
from typing import Annotated, Self
from urllib.parse import urlsplit

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

MANIFEST_VERSION = 1
# Versión del formato de config.json de Backrest v1.14.1 (nº de migraciones en
# internal/config/migrations: 001…006); una menor haría que Backrest migre en el arranque.
BACKREST_CONFIG_VERSION = 6
# Volúmenes con nombre del compose de la caja que se pueden copiar. Los volúmenes de
# configuración y datos de Backrest quedan fuera: el primero guarda secretos.
ALLOWED_VOLUMES = frozenset({"data"})
SOURCE_ROOT = "/source"
# compose.yaml de la caja ya monta `data` en `:ro` en /source/caja; el paquete no lo vuelve a montar
# (box.sh rechaza que el override toque /source/caja).
VOLUME_MOUNTS = {"data": f"{SOURCE_ROOT}/caja"}
_FORBIDDEN_TREES = ("/proc", "/sys", "/dev")
_DOCKER_SOCKETS = ("/var/run/docker.sock", "/run/docker.sock")
_SECRET_CHARS = re.compile(r"[A-Za-z0-9/+=_.\-]{8,256}")
_ID = re.compile(r"[a-z][a-z0-9_]{1,30}")
_CRON_FIELD = re.compile(r"[0-9*/,\-]+")
_BUCKET = re.compile(r"[a-z0-9][a-z0-9.\-]{1,61}[a-z0-9]")
_PREFIX_SEGMENT = re.compile(r"[A-Za-z0-9._\-]+")
_PRUNE_CRON = "0 4 * * 0"
_CHECK_CRON = "0 5 1 * *"

Days = Annotated[int, Field(gt=0, strict=True)]
DEFAULT_MIN_CHECK_READ_DATA_PERCENT = 5
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class BackupInputError(ValueError):
    """Entrada no válida; el mensaje está en castellano y no incluye secretos."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class S3Destination(_Strict):
    endpoint: str
    bucket: str
    prefix: str = ""
    lock_days: Days

    @field_validator("endpoint")
    @classmethod
    def _endpoint(cls, value: str) -> str:
        parts = urlsplit(value.strip())
        if parts.scheme != "https" or not parts.hostname:
            raise ValueError("el endpoint S3 debe ser una dirección https://servidor")
        if parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError("el endpoint S3 no puede llevar usuario, contraseña ni parámetros")
        if parts.path not in ("", "/"):
            raise ValueError("el endpoint S3 no lleva ruta: el cubo y el prefijo se piden aparte")
        return f"https://{parts.netloc}"

    @field_validator("bucket")
    @classmethod
    def _bucket(cls, value: str) -> str:
        if not _BUCKET.fullmatch(value):
            raise ValueError("el nombre del cubo no es válido (3-63 caracteres a-z, 0-9, . o -)")
        return value

    @field_validator("prefix")
    @classmethod
    def _prefix(cls, value: str) -> str:
        value = value.strip().strip("/")
        for segment in value.split("/") if value else []:
            if segment in (".", "..") or not _PREFIX_SEGMENT.fullmatch(segment):
                raise ValueError("el prefijo solo admite letras, cifras, . _ - y /, sin '..'")
        return value

    @property
    def repository(self) -> str:
        """URI del repositorio en la sintaxis de restic (sin credenciales)."""
        path = f"{self.bucket}/{self.prefix}" if self.prefix else self.bucket
        return f"s3:{self.endpoint}/{path}"


class Schedule(_Strict):
    cron: str
    retention_days: Days
    max_backup_age_hours: Days
    max_check_age_days: Days
    restore_test_validity_days: Days
    # parte de los datos que la app relee en cada recogida; se emite siempre en backups.yaml
    min_check_read_data_percent: Annotated[int, Field(ge=0, le=100, strict=True)] = (
        DEFAULT_MIN_CHECK_READ_DATA_PERCENT
    )

    @field_validator("cron")
    @classmethod
    def _cron(cls, value: str) -> str:
        fields = value.split()
        if len(fields) != 5 or not all(_CRON_FIELD.fullmatch(f) for f in fields):
            raise ValueError("el cron debe tener 5 campos (minuto hora día mes día-semana)")
        return " ".join(fields)


def normalize_host_path(value: str) -> str:
    """Ruta absoluta del host, normalizada; rechaza lo que no se puede copiar."""
    if not value.startswith("/"):
        raise ValueError(f"{value!r}: la ruta debe ser absoluta")
    if any(ord(c) < 32 or c in ":\\\"'" for c in value):
        raise ValueError(f"{value!r}: la ruta tiene caracteres no admitidos")
    parts = PurePosixPath(value).parts
    if ".." in parts:
        raise ValueError(f"{value!r}: la ruta no puede contener '..'")
    path = str(PurePosixPath(value))
    if path == "/":
        raise ValueError("no se puede copiar la raíz '/': elige carpetas concretas")
    for tree in _FORBIDDEN_TREES:
        if path == tree or path.startswith(tree + "/"):
            raise ValueError(f"{path}: {tree} no se puede copiar")
    for sock in _DOCKER_SOCKETS:
        if sock == path or sock.startswith(path + "/") or path.startswith(sock + "/"):
            raise ValueError(f"{path}: contiene o es el socket de Docker, que no se puede copiar")
    return path


class BackupInputs(_Strict):
    id: str
    asset_id: Text
    destination: S3Destination
    schedule: Schedule
    volumes: tuple[str, ...] = ()
    host_paths: tuple[str, ...] = ()
    instance: str = "caja"

    @field_validator("id", "instance")
    @classmethod
    def _identifier(cls, value: str) -> str:
        if not _ID.fullmatch(value):
            raise ValueError("el identificador usa minúsculas, cifras y _ (2-31 caracteres)")
        return value

    @field_validator("volumes")
    @classmethod
    def _volumes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        unknown = sorted(set(value) - ALLOWED_VOLUMES)
        if unknown:
            allowed = ", ".join(sorted(ALLOWED_VOLUMES))
            raise ValueError(f"volumen no permitido: {', '.join(unknown)} (permitidos: {allowed})")
        return tuple(dict.fromkeys(value))

    @field_validator("host_paths")
    @classmethod
    def _host_paths(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(normalize_host_path(p) for p in value))

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if not self.volumes and not self.host_paths:
            raise ValueError("elige qué se copia: al menos un volumen o una ruta")
        return self

    @property
    def notices(self) -> tuple[str, ...]:
        """Avisos informativos (no rechazan nada) para mostrar junto al formulario."""
        lock, retention = self.destination.lock_days, self.schedule.retention_days
        if lock <= 0:
            return ()
        return (
            f"Con bloqueo de objetos ({lock} días), lo que Backrest pode tras {retention} días "
            f"sigue ocupando espacio en el destino hasta que vence el bloqueo: "
            "el borrado solo deja una marca y la versión protegida se conserva.",
        )

    @property
    def env_prefix(self) -> str:
        return f"ENS_BACKUPS_{self.id.upper()}"


def _secret(value: SecretStr, name: str) -> SecretStr:
    if not _SECRET_CHARS.fullmatch(value.get_secret_value()):
        raise ValueError(f"{name}: debe tener 8-256 caracteres de A-Z a-z 0-9 / + = _ . -")
    return value


class BundleSecrets(_Strict):
    repo_password: SecretStr
    write_access_key_id: SecretStr
    write_secret_access_key: SecretStr
    read_access_key_id: SecretStr
    read_secret_access_key: SecretStr

    @model_validator(mode="after")
    def _valid(self) -> Self:
        for name in type(self).model_fields:
            _secret(getattr(self, name), name)
        if (
            self.write_access_key_id == self.read_access_key_id
            or self.write_secret_access_key == self.read_secret_access_key
        ):
            raise ValueError(
                "las claves S3 de lectura (para la app) deben ser distintas de las de "
                "escritura (para Backrest): si no, la app podría borrar copias"
            )
        return self


def generate_repo_password() -> SecretStr:
    return SecretStr(secrets.token_urlsafe(32))


def _repo_uri(inputs: BackupInputs) -> str:
    return inputs.destination.repository


def _backrest_config(inputs: BackupInputs, secret: BundleSecrets, paths: list[str]) -> dict:
    schedule = inputs.schedule
    return {
        "modno": 1,
        "version": BACKREST_CONFIG_VERSION,
        "instance": inputs.instance,
        "repos": [
            {
                "id": inputs.id,
                "uri": _repo_uri(inputs),
                "password": secret.repo_password.get_secret_value(),
                "env": [
                    f"AWS_ACCESS_KEY_ID={secret.write_access_key_id.get_secret_value()}",
                    f"AWS_SECRET_ACCESS_KEY={secret.write_secret_access_key.get_secret_value()}",
                ],
                "autoInitialize": True,
                "prunePolicy": {
                    "schedule": {"cron": _PRUNE_CRON},
                    "maxUnusedPercent": 10,
                },
                "checkPolicy": {
                    "schedule": {"cron": _CHECK_CRON},
                    "structureOnly": True,
                },
            }
        ],
        "plans": [
            {
                "id": f"{inputs.id}_plan",
                "repo": inputs.id,
                "paths": paths,
                "schedule": {"cron": schedule.cron},
                "retention": {"policyTimeBucketed": {"daily": schedule.retention_days}},
            }
        ],
    }


def _mount_target(host_path: str) -> str:
    return f"{SOURCE_ROOT}/host{host_path}"


def _container_paths(inputs: BackupInputs) -> list[str]:
    return [VOLUME_MOUNTS[v] for v in inputs.volumes] + [
        _mount_target(p) for p in inputs.host_paths
    ]


def _compose_override(inputs: BackupInputs) -> dict:
    mounts = [f"{p}:{_mount_target(p)}:ro" for p in inputs.host_paths]
    return {"services": {"backrest": {"volumes": mounts}}}


def _app_env(inputs: BackupInputs, secret: BundleSecrets) -> str:
    p = inputs.env_prefix
    return (
        f"{p}_RESTIC_PASSWORD={secret.repo_password.get_secret_value()}\n"
        f"{p}_AWS_ACCESS_KEY_ID={secret.read_access_key_id.get_secret_value()}\n"
        f"{p}_AWS_SECRET_ACCESS_KEY={secret.read_secret_access_key.get_secret_value()}\n"
    )


def _backups_fragment(inputs: BackupInputs) -> str:
    s = inputs.schedule
    entry = {
        "id": inputs.id,
        "asset_id": inputs.asset_id,
        "repository": _repo_uri(inputs),
        "max_backup_age_hours": s.max_backup_age_hours,
        "max_check_age_days": s.max_check_age_days,
        "restore_test_validity_days": s.restore_test_validity_days,
        "min_check_read_data_percent": s.min_check_read_data_percent,
    }
    return yaml.safe_dump({"repositories": [entry]}, sort_keys=False, allow_unicode=True)


def _json(data: dict) -> str:
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _yaml(data: dict) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def _manifest(files: dict[str, bytes]) -> bytes:
    lines = [f"version: {MANIFEST_VERSION}"]
    lines += [f"{hashlib.sha256(body).hexdigest()}  {name}" for name, body in files.items()]
    return ("\n".join(lines) + "\n").encode()


_PRIVATE = ("copias/config.json", "copias/app.env")


def _pack(files: dict[str, bytes], now: datetime) -> bytes:
    mtime = int(now.timestamp())
    files = {**files, "copias/MANIFEST": _manifest(files)}
    raw = io.BytesIO()
    with (
        gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=mtime, compresslevel=9) as gz,
        tarfile.open(fileobj=gz, mode="w", format=tarfile.GNU_FORMAT) as tar,
    ):
        for name, body in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(body)
            info.mtime = mtime
            info.mode = 0o600 if name in _PRIVATE else 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            tar.addfile(info, io.BytesIO(body))
    return raw.getvalue()


def _checked(now: datetime) -> None:
    if now.tzinfo is None:
        raise BackupInputError("la fecha del paquete debe llevar zona horaria")


def render_bundle(inputs: BackupInputs, secret: BundleSecrets, now: datetime) -> bytes:
    """Paquete para la caja: Backrest en el VPS, copiando volúmenes y rutas del propio VPS."""
    _checked(now)
    files = {
        "copias/config.json": _json(
            _backrest_config(inputs, secret, _container_paths(inputs))
        ).encode(),
        "copias/compose.yaml": _yaml(_compose_override(inputs)).encode(),
        "copias/app.env": _app_env(inputs, secret).encode(),
        "copias/backups.yaml": _backups_fragment(inputs).encode(),
    }
    return _pack(files, now)


def _host_instructions(inputs: BackupInputs) -> str:
    return f"""# Copias de «{inputs.instance}» en otro servidor Linux

Repositorio propio: {_repo_uri(inputs)} (id «{inputs.id}»),
en el mismo destino que las demás copias.

1. Instala Backrest v1.14.1 con el script de instalación de su README
   (https://github.com/garethgeorge/backrest).
   Fija `BACKREST_RESTIC_COMMAND` a un binario de restic que hayas verificado.
2. Copia `config.json` a la ruta de configuración de Backrest de ese servidor (variable
   `BACKREST_CONFIG`) con permisos 600 y propietario el usuario que ejecuta Backrest.
   Contiene la contraseña del repositorio y las claves S3 de escritura:
   no lo guardes en ningún otro sitio.
3. No expongas Backrest a internet: accede por túnel SSH. En el primer acceso crea el usuario
   de Backrest, antes de hacer nada más.
4. Comprueba que el usuario de Backrest puede leer {", ".join(inputs.host_paths)}.
5. En la caja: añade `backups.yaml` a `connectors/backups.yaml` y `app.env` al entorno de la app.
6. Tras la primera copia, haz y registra una prueba de restauración (revisión humana).
"""


def render_host_bundle(inputs: BackupInputs, secret: BundleSecrets, now: datetime) -> bytes:
    """Paquete para otro servidor Linux con Backrest propio y repositorio propio."""
    _checked(now)
    if inputs.volumes:
        raise BackupInputError("otro servidor no tiene los volúmenes de la caja: usa solo rutas")
    files = {
        "copias/config.json": _json(
            _backrest_config(inputs, secret, list(inputs.host_paths))
        ).encode(),
        "copias/app.env": _app_env(inputs, secret).encode(),
        "copias/backups.yaml": _backups_fragment(inputs).encode(),
        "copias/INSTRUCCIONES.md": _host_instructions(inputs).encode(),
    }
    return _pack(files, now)


def validation_messages(exc: ValidationError) -> list[str]:
    """Mensajes en castellano de un ValidationError, sin los valores recibidos."""
    out = []
    for item in exc.errors(include_input=False, include_url=False):
        where = ".".join(str(p) for p in item["loc"])
        msg = item["msg"].removeprefix("Value error, ")
        out.append(f"{where}: {msg}" if where else msg)
    return out
