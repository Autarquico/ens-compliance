"""Lock del ciclo: un único propietario por workspace en esta máquina.

Módulo hoja (solo depende de `workspace.settings`): lo usan `backup/`, la CLI y `scheduling/`.
Solo lo adquieren las entradas externas; las etapas reciben un `CycleContext`.
El fichero de lock nunca se borra ni se sustituye: lo libera el kernel al morir el propietario.
"""

import fcntl
import hashlib
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from types import TracebackType
from typing import Protocol, Self

from ens_evidence.workspace.settings import PRODUCT_NAME, Settings

STATE_DIR_ENV = "ENS_STATE_DIR"
DEFAULT_STATE_DIR = Path(f"~/.local/state/{PRODUCT_NAME}")
POLL_SECONDS = 5.0
_OTHERS_CAN_WRITE = 0o022


class CycleBusy(Exception):
    """Otro proceso tiene el ciclo de este workspace."""


class Clock(Protocol):
    """El reloj del ciclo: la hora y una espera que quien orquesta puede interrumpir."""

    def now(self) -> datetime:
        """Instante actual con zona horaria."""
        ...

    def sleep(self, seconds: float) -> None:
        """Espera hasta `seconds`; puede volver antes o lanzar si se pide parar."""
        ...


def workspace_identity(settings: Settings) -> str:
    """Identidad canónica del destino: `realpath` local o `s3://bucket/prefix`."""
    workspace = settings.workspace
    if workspace.backend == "s3":
        s3 = workspace.s3
        assert s3 is not None
        prefix = "/".join(part for part in s3.prefix.split("/") if part)
        return f"s3://{s3.bucket}/{prefix}" if prefix else f"s3://{s3.bucket}"
    assert workspace.local is not None
    return os.path.realpath(workspace.local.path.expanduser())


def resolve_state_dir(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    configured = env.get(STATE_DIR_ENV)
    return Path(configured).expanduser() if configured else DEFAULT_STATE_DIR.expanduser()


def _private_directory(path: Path) -> None:
    """Crea el directorio de estado y exige que sea del usuario y que nadie más escriba en él."""
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.stat()
    if info.st_uid != os.geteuid() or info.st_mode & _OTHERS_CAN_WRITE:
        raise PermissionError("el directorio de estado debe ser del usuario y no escribible")


def _open_lock_file(path: Path) -> int:
    """Abre el fichero sin seguir enlaces y comprueba que es un fichero propio."""
    flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
            raise PermissionError("el fichero de lock no es un fichero propio")
    except BaseException:
        os.close(fd)
        raise
    return fd


class CycleLock:
    def __init__(self, identity: str, state_dir: Path | None = None) -> None:
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
        self._path = (state_dir or resolve_state_dir()) / f"cycle-{digest}.lock"
        self._fd: int | None = None

    @classmethod
    def for_settings(cls, settings: Settings, env: Mapping[str, str] | None = None) -> "CycleLock":
        return cls(workspace_identity(settings), resolve_state_dir(env))

    @property
    def path(self) -> Path:
        return self._path

    @property
    def held(self) -> bool:
        return self._fd is not None

    def acquire(self) -> Self:
        if self._fd is not None:
            raise RuntimeError("el lock ya está adquirido por este objeto")
        _private_directory(self._path.parent)
        fd = _open_lock_file(self._path)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise CycleBusy("el ciclo de este workspace está en curso") from None
        except BaseException:
            os.close(fd)
            raise
        self._fd = fd
        return self

    def release(self) -> None:
        fd, self._fd = self._fd, None
        if fd is not None:
            os.close(fd)

    def __enter__(self) -> Self:
        return self.acquire()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()


@dataclass(frozen=True)
class CycleContext:
    """Prueba de que el lock ya está tomado; las etapas lo reciben, no lo piden."""

    lock: CycleLock

    def __post_init__(self) -> None:
        if not self.lock.held:
            raise ValueError("CycleContext exige un lock adquirido")


def acquire_or_wait(lock: CycleLock, clock: Clock, wait_minutes: float) -> CycleLock:
    """Reintenta hasta `wait_minutes`; agotado el plazo relanza `CycleBusy`."""
    deadline = clock.now() + timedelta(minutes=wait_minutes)
    while True:
        try:
            return lock.acquire()
        except CycleBusy:
            remaining = (deadline - clock.now()).total_seconds()
            if remaining <= 0:
                raise
            clock.sleep(min(POLL_SECONDS, remaining))
