"""Exportador del kit de contraseñas: de la base de Vaultwarden a `usuarios.json`, sin cofres.

Contrato en `.claude/loop/kit-passwords/plan.md` (§C5, C6, C8, C11). Solo lee `users`, `twofactor`
y `devices` y nunca una columna personal (email, nombre) ni un secreto. Completo o nada: ante un
error o una fila no interpretable no publica nada y el export anterior queda intacto.
Los errores no incluyen valores de la base.
"""

import contextlib
import json
import os
import re
import shutil
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Self

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    ValidationError,
    model_validator,
)

EXPORT_FILE = "usuarios.json"
DB_FILE = "db.sqlite3"
FORMAT_VERSION = 1
SNAPSHOT_PREFIX = "db-"
SNAPSHOT_SUFFIX = ".sqlite3"
SNAPSHOT_STAMP = "%Y%m%dT%H%M%SZ"
EXTRAS_PREFIX = "extras-"
EXTRA_TREES = ("attachments", "sends")
EXTRA_FILES = ("config.json",)
# Tope duro: pasarlo hace fallar la instantánea (snapshot_ok false); nunca copia a medias.
MAX_EXTRA_FILES = 100_000
MAX_EXTRA_BYTES = 10 * 1024**3
KNOWN_MIGRATIONS = frozenset({"20261007120000"})  # Vaultwarden 1.37.5, verificada en T0
SEND_TYPE_FILE = 1  # SendType::File en Vaultwarden 1.37.5
SECOND_FACTOR_TYPES = frozenset({0, 1, 2, 3, 4, 6, 7})  # 5 recordar, 8 recuperación, >=1000 no


class PasswordsExportError(Exception):
    """Fallo del exportador; el mensaje es fijo y nunca lleva valores de la base."""


def _utc(value: datetime) -> datetime:
    if value.utcoffset() != UTC.utcoffset(None):
        raise ValueError("la fecha debe estar en UTC")
    return value


UtcDatetime = Annotated[AwareDatetime, AfterValidator(_utc)]


class ExportedUser(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: uuid.UUID
    enabled: StrictBool
    invited: StrictBool
    created_at: UtcDatetime
    second_factor: StrictBool
    last_active: UtcDatetime | None


class PasswordsExport(BaseModel):
    """Modelo único del export: lo escribe el exportador y lo lee el conector."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    format: Annotated[int, Field(strict=True)]
    exported_at: UtcDatetime
    vaultwarden_migration: Annotated[str, Field(min_length=1, max_length=50)]
    snapshot_ok: StrictBool
    last_good_snapshot_at: UtcDatetime | None
    users: tuple[ExportedUser, ...]

    @model_validator(mode="after")
    def _coherent(self) -> Self:
        if self.format != FORMAT_VERSION:
            raise ValueError("formato de export desconocido")
        if len({u.id for u in self.users}) != len(self.users):
            raise ValueError("ids de usuario repetidos")
        if self.snapshot_ok and self.last_good_snapshot_at is None:
            raise ValueError("una instantánea correcta lleva fecha")
        dates = [self.last_good_snapshot_at]
        for user in self.users:
            dates += [user.created_at, user.last_active]
        if any(d is not None and d > self.exported_at for d in dates):
            raise ValueError("fecha posterior al momento del export")
        return self


# --- lectura de la base -------------------------------------------------------------------


_DATE = re.compile(r"(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?")


def _parse_date(value: object) -> datetime:
    """Vaultwarden guarda UTC ingenuo, con hasta 9 decimales: se recorta a microsegundos."""
    match = _DATE.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise ValueError("fecha no interpretable")
    day, clock, fraction = match.groups()
    micros = (fraction or "").ljust(6, "0")[:6]
    return datetime.fromisoformat(f"{day}T{clock}.{micros}").replace(tzinfo=UTC)


def _flag(value: object) -> bool:
    if type(value) is not int or value not in (0, 1):
        raise ValueError("booleano no interpretable")
    return bool(value)


def _read_users(conn: sqlite3.Connection) -> tuple[str, list[ExportedUser]]:
    versions = [r[0] for r in conn.execute("SELECT version FROM __diesel_schema_migrations")]
    if not versions or not all(isinstance(v, str) for v in versions):
        raise PasswordsExportError("la base no declara su migración")
    migration = max(versions)
    if migration not in KNOWN_MIGRATIONS:
        raise PasswordsExportError("migración de Vaultwarden desconocida; no se exporta")

    second_factor: set[str] = set()
    for user_uuid, atype, enabled in conn.execute(
        "SELECT user_uuid, atype, enabled FROM twofactor"
    ):
        if type(atype) is not int:
            raise ValueError("tipo no interpretable")
        if _flag(enabled) and atype in SECOND_FACTOR_TYPES:
            second_factor.add(user_uuid)

    last_active: dict[str, datetime] = {}
    for user_uuid, updated_at in conn.execute("SELECT user_uuid, updated_at FROM devices"):
        seen = _parse_date(updated_at)
        last_active[user_uuid] = max(seen, last_active.get(user_uuid, seen))

    users = []
    for raw_id, created_at, enabled, hash_type, hash_length in conn.execute(
        "SELECT uuid, created_at, enabled, typeof(password_hash), length(password_hash) "
        "FROM users ORDER BY uuid"
    ):
        if hash_type != "blob":
            raise ValueError("hash no interpretable")
        users.append(
            ExportedUser(
                id=uuid.UUID(raw_id),
                enabled=_flag(enabled),
                invited=hash_length == 0,
                created_at=_parse_date(created_at),
                second_factor=raw_id in second_factor,
                last_active=last_active.get(raw_id),
            )
        )
    return migration, users


def _read_snapshot_source(db_path: Path) -> sqlite3.Connection:
    # Solo `mode=ro`: `immutable=1` ignora el WAL y devuelve datos viejos sin avisar (T0).
    # Autocommit: `VACUUM INTO` no puede ir dentro de una transacción.
    return sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True, isolation_level=None)


def _read_export_data(conn: sqlite3.Connection) -> tuple[str, list[ExportedUser]]:
    conn.execute("BEGIN")
    try:
        return _read_users(conn)
    finally:
        conn.execute("ROLLBACK")


# --- instantánea --------------------------------------------------------------------------


def _fsync_dir(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_file(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _snapshots(snapshot_dir: Path) -> list[tuple[datetime, Path]]:
    found = []
    for path in snapshot_dir.glob(f"{SNAPSHOT_PREFIX}*{SNAPSHOT_SUFFIX}"):
        stamp = path.name[len(SNAPSHOT_PREFIX) : -len(SNAPSHOT_SUFFIX)]
        with contextlib.suppress(ValueError):
            found.append((datetime.strptime(stamp, SNAPSHOT_STAMP).replace(tzinfo=UTC), path))
    return sorted(found)


def last_good_snapshot(snapshot_dir: Path) -> datetime | None:
    found = _snapshots(snapshot_dir) if snapshot_dir.is_dir() else []
    return found[-1][0] if found else None


def _integrity_ok(path: Path) -> bool:
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        return conn.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    finally:
        conn.close()


def _publish_copy(source: Path, target: Path) -> None:
    tmp = target.with_name(f".{target.name}.tmp")
    try:
        shutil.copyfile(source, tmp)
        tmp.chmod(0o600)
        _fsync_file(tmp)
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)


def _copy_extra_file(source: Path, target: Path, budget: list[int]) -> None:
    if source.is_symlink() or not source.is_file():
        raise PasswordsExportError("la instantánea solo admite ficheros y directorios normales")
    budget[0] += 1
    budget[1] += source.stat().st_size
    if budget[0] > MAX_EXTRA_FILES or budget[1] > MAX_EXTRA_BYTES:
        raise PasswordsExportError(
            "adjuntos, sends y configuración superan el tope de la instantánea"
        )
    shutil.copyfile(source, target)
    target.chmod(0o600)
    _fsync_file(target)


def _copy_extra_tree(source: Path, target: Path, budget: list[int]) -> None:
    if source.is_symlink() or not source.is_dir():
        raise PasswordsExportError("la instantánea solo admite ficheros y directorios normales")
    target.mkdir(mode=0o700)
    for entry in sorted(source.iterdir()):
        if entry.is_symlink():
            raise PasswordsExportError("la instantánea no admite enlaces simbólicos")
        if entry.is_dir():
            _copy_extra_tree(entry, target / entry.name, budget)
        else:
            _copy_extra_file(entry, target / entry.name, budget)
    target.chmod(0o700)


def _stage_extras(data_dir: Path, stage: Path) -> None:
    """Copia lo que una restauración necesita además de la base; lo que no exista se omite."""
    stage.mkdir(mode=0o700)
    budget = [0, 0]
    for name in EXTRA_TREES:
        source = data_dir / name
        if source.is_symlink() or source.exists():
            _copy_extra_tree(source, stage / name, budget)
    for name in EXTRA_FILES:
        source = data_dir / name
        if source.is_symlink() or source.exists():
            _copy_extra_file(source, stage / name, budget)


def _send_file_path(send_uuid: str, data: object) -> Path | None:
    """`sends/<send_uuid>/<file_id>`; `file_id` es el `id` del JSON de `sends.data` (1.37.5)."""
    try:
        file_id = json.loads(data)["id"]  # type: ignore[arg-type]
    except (TypeError, ValueError, KeyError):
        return None
    if not isinstance(file_id, str) or not file_id:
        return None
    return Path(send_uuid) / file_id


def _referenced_extras(db_copy: Path) -> list[Path | None]:
    """Rutas relativas a `data_dir` de los ficheros que la base capturada da por existentes.

    `None` marca una fila de send de fichero cuya ruta no se puede deducir.
    """
    conn = sqlite3.connect(f"{db_copy.resolve().as_uri()}?mode=ro", uri=True)
    try:
        paths: list[Path | None] = [
            Path("attachments") / cipher_uuid / attachment_id
            for cipher_uuid, attachment_id in conn.execute(
                "SELECT cipher_uuid, id FROM attachments"
            )
        ]
        for send_uuid, data in conn.execute(
            "SELECT uuid, data FROM sends WHERE atype = ?", (SEND_TYPE_FILE,)
        ):
            send_path = _send_file_path(send_uuid, data)
            paths.append(None if send_path is None else Path("sends") / send_path)
        return paths
    finally:
        conn.close()


def _check_extras_cover_db(db_copy: Path, stage: Path) -> None:
    """Falla si la base capturada referencia ficheros que no llegaron al stage (borrados en medio).

    Ficheros de más en el stage no son error: llegaron después de la captura.
    """
    missing = sum(
        1 for rel in _referenced_extras(db_copy) if rel is None or not (stage / rel).is_file()
    )
    if missing:
        raise PasswordsExportError(
            f"faltan {missing} ficheros de adjuntos o sends referenciados por la base capturada"
        )


def _extras_dir(snapshot_dir: Path, stamp: str) -> Path:
    return snapshot_dir / f"{EXTRAS_PREFIX}{stamp}"


def _take_snapshot(
    conn: sqlite3.Connection, data_dir: Path, snapshot_dir: Path, now: datetime
) -> None:
    """Publica `db-<fecha>.sqlite3` el último: es la marca de instantánea completa.

    Antes quedan listos `extras-<fecha>/` (adjuntos, sends, config.json) y las `rsa_key*`.
    Si algo falla no se publica la marca y las anteriores siguen intactas.
    """
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    stamp = now.astimezone(UTC).strftime(SNAPSHOT_STAMP)
    tmp = snapshot_dir / f".{DB_FILE}.{os.getpid()}.tmp"
    stage = snapshot_dir / f".extras.{os.getpid()}.tmp"
    extras = _extras_dir(snapshot_dir, stamp)
    final = snapshot_dir / f"{SNAPSHOT_PREFIX}{stamp}{SNAPSHOT_SUFFIX}"
    tmp.unlink(missing_ok=True)
    shutil.rmtree(stage, ignore_errors=True)
    published_extras = False
    try:
        conn.execute("VACUUM INTO ?", (str(tmp),))
        if not _integrity_ok(tmp):
            raise PasswordsExportError("la instantánea no pasa integrity_check")
        tmp.chmod(0o600)
        _fsync_file(tmp)
        _stage_extras(data_dir, stage)
        _check_extras_cover_db(tmp, stage)
        for key in sorted(data_dir.glob("rsa_key*")):
            if key.is_file() and not key.is_symlink():
                _publish_copy(key, snapshot_dir / key.name)
        shutil.rmtree(extras, ignore_errors=True)
        os.replace(stage, extras)
        published_extras = True
        _fsync_dir(snapshot_dir)
        os.replace(tmp, final)
    except BaseException:
        if published_extras and not final.exists():
            shutil.rmtree(extras, ignore_errors=True)
        raise
    finally:
        tmp.unlink(missing_ok=True)
        shutil.rmtree(stage, ignore_errors=True)
    _fsync_dir(snapshot_dir)
    for _, old in _snapshots(snapshot_dir):
        if old != final:
            old.unlink(missing_ok=True)
    for leftover in snapshot_dir.glob(f"{EXTRAS_PREFIX}*"):
        if leftover != extras:
            shutil.rmtree(leftover, ignore_errors=True)


# --- publicación --------------------------------------------------------------------------


def _publish_export(model: PasswordsExport, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / EXPORT_FILE
    tmp = out_dir / f".{EXPORT_FILE}.{os.getpid()}.tmp"
    try:
        with tmp.open("wb") as handle:
            handle.write(model.model_dump_json().encode())
            handle.flush()
            os.fsync(handle.fileno())
        tmp.chmod(0o644)
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)
    _fsync_dir(out_dir)


def _describe(error: ValidationError) -> str:
    fields = sorted({".".join(str(p) for p in e["loc"]) or "-" for e in error.errors()})
    return f"fila no interpretable (campos: {', '.join(fields)})"


def export(db_path: Path, out_dir: Path, snapshot_dir: Path, now: datetime) -> PasswordsExport:
    """Una vuelta: instantánea obligatoria y `usuarios.json`. Lanza `PasswordsExportError`."""
    if not db_path.is_file():
        raise PasswordsExportError("no existe la base de Vaultwarden")
    snapshot_ok = True
    try:
        conn = _read_snapshot_source(db_path)
    except sqlite3.Error:
        raise PasswordsExportError("no se pudo abrir la base en solo lectura") from None
    try:
        try:
            _take_snapshot(conn, db_path.parent, snapshot_dir, now)
        except (OSError, sqlite3.Error, PasswordsExportError):
            snapshot_ok = False
        try:
            migration, users = _read_export_data(conn)
        except PasswordsExportError:
            raise
        except ValidationError as error:
            raise PasswordsExportError(_describe(error)) from None
        except sqlite3.Error:
            raise PasswordsExportError("lectura de la base fallida") from None
        except (ValueError, TypeError, AttributeError):
            raise PasswordsExportError("fila no interpretable") from None
    finally:
        conn.close()
    try:
        model = PasswordsExport(
            format=FORMAT_VERSION,
            exported_at=now,
            vaultwarden_migration=migration,
            snapshot_ok=snapshot_ok,
            last_good_snapshot_at=last_good_snapshot(snapshot_dir),
            users=tuple(users),
        )
    except ValidationError as error:
        raise PasswordsExportError(_describe(error)) from None
    try:
        _publish_export(model, out_dir)
    except OSError:
        raise PasswordsExportError("no se pudo publicar el export") from None
    return model


_INTERVAL = re.compile(r"(\d+)([smh])")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600}


def parse_interval(text: str) -> int:
    """`15m`, `30s`, `1h` → segundos; siempre mayor que cero."""
    match = _INTERVAL.fullmatch(text.strip())
    seconds = int(match[1]) * _UNIT_SECONDS[match[2]] if match else 0
    if seconds <= 0:
        raise ValueError("intervalo no válido: use un número seguido de s, m o h (por ejemplo 15m)")
    return seconds


__all__ = [
    "KNOWN_MIGRATIONS",
    "ExportedUser",
    "PasswordsExport",
    "PasswordsExportError",
    "export",
    "last_good_snapshot",
    "parse_interval",
]
