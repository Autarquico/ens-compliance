"""Backend de carpeta local con contención verificada."""

import errno
import fcntl
import os
import secrets
import stat
import threading
from collections.abc import Iterator, MutableSequence
from contextlib import contextmanager
from pathlib import Path

from ens_evidence.workspace.errors import (
    AccessDenied,
    AlreadyExists,
    NotFound,
    NotText,
    StorageUnavailable,
)
from ens_evidence.workspace.keys import normalize_key


class LocalStore:
    def __init__(self, root: Path) -> None:
        self._root = Path(root).resolve()
        self._root_fd: int | None = None
        self._closed = False
        self._opening = threading.Lock()
        if _DIR_FD_SUPPORTED and self._root.is_dir():
            self._root_fd = _walk_root(self._root, create=False)

    def close(self) -> None:
        """Libera el descriptor de la raíz; las operaciones posteriores fallan cerradas."""
        fd, self._root_fd, self._closed = self._root_fd, None, True
        if fd is not None:
            os.close(fd)

    def __enter__(self) -> "LocalStore":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def read_text(self, key: str) -> str:
        try:
            return self.read_bytes(key).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise NotText("el fichero no es texto UTF-8") from exc

    def read_bytes(self, key: str, limit: int | None = None) -> bytes:
        path = self._contained(normalize_key(key))
        try:
            if limit is None:
                return path.read_bytes()
            with path.open("rb") as handle:
                return handle.read(max(limit, 0))
        except (FileNotFoundError, IsADirectoryError, NotADirectoryError) as exc:
            raise NotFound("clave no encontrada") from exc
        except OSError as exc:
            raise _translate(exc) from exc

    def write_text(self, key: str, text: str) -> None:
        self.write_bytes(key, text.encode("utf-8"))

    def write_bytes(self, key: str, data: bytes) -> None:
        try:
            with self._parent(normalize_key(key)) as (fd, leaf):
                _refuse_link(fd, leaf)
                tmp_name = _stage(fd, data)
                try:
                    os.replace(tmp_name, leaf, src_dir_fd=fd, dst_dir_fd=fd)
                except BaseException:
                    _discard(fd, tmp_name)
                    raise
        except OSError as exc:
            raise _translate(exc) from exc

    def create_bytes(self, key: str, data: bytes) -> None:
        try:
            with self._parent(normalize_key(key)) as (fd, leaf):
                _refuse_link(fd, leaf)
                tmp_name = _stage(fd, data)
                try:
                    os.link(tmp_name, leaf, src_dir_fd=fd, dst_dir_fd=fd)
                finally:
                    _discard(fd, tmp_name)
        except FileExistsError as exc:
            raise AlreadyExists("la clave ya existe") from exc
        except OSError as exc:
            raise _translate(exc) from exc

    @contextmanager
    def exclusive_lock(self, key: str) -> Iterator[None]:
        """`flock` exclusivo sobre el fichero `key` (se crea si falta y no se borra: lo libera el
        kernel si el proceso muere), con la misma contención que las demás escrituras."""
        try:
            with self._parent(normalize_key(key)) as (parent, leaf):
                fd = os.open(
                    leaf,
                    os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=parent,
                )
        except OSError as exc:
            raise _translate(exc) from exc
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def _root_descriptor(self) -> int:
        """La raíz se abre una sola vez: al construir si ya existía, o en la primera escritura si
        no (leer nunca la crea). Después, sustituir su ruta no cambia dónde se escribe."""
        if self._closed or not _DIR_FD_SUPPORTED:
            raise StorageUnavailable("almacenamiento no disponible: sin descriptor de raíz")
        with self._opening:
            if self._root_fd is None:
                self._root_fd = _walk_root(self._root, create=True)
            return self._root_fd

    @contextmanager
    def _parent(self, key: str) -> Iterator[tuple[int, str]]:
        """Descriptor del directorio padre de `key` y el nombre de la hoja. Se parte del descriptor
        de raíz abierto al construir (la ruta de la raíz no se reabre) y se abre cada componente
        relativo al anterior con `O_NOFOLLOW`, creando los que faltan: nada se resuelve dos veces,
        así que un directorio sustituido por un enlace entre pasos no puede sacar la escritura
        del workspace."""
        *parents, leaf = key.split("/")
        fd = os.dup(self._root_descriptor())
        try:
            for name in parents:
                child = _descend(fd, name)
                os.close(fd)
                fd = child
            yield fd, leaf
        finally:
            os.close(fd)

    def exists(self, key: str) -> bool:
        return self._contained(normalize_key(key)).is_file()

    def list(self, prefix: str = "") -> list[str]:
        base = normalize_key(prefix, allow_empty=True)
        start = self._contained(base) if base else self._root
        keys: list[str] = []
        try:
            self._walk(start, keys)
        except OSError as exc:
            raise _translate(exc) from exc
        return sorted(keys)

    def _walk(self, directory: Path, keys: MutableSequence[str]) -> None:
        if not directory.is_dir():
            return
        for entry in os.scandir(directory):
            path = Path(entry.path)
            self._check(path)
            if entry.is_symlink():
                if path.is_file():
                    keys.append(self._key_of(path))
            elif entry.is_dir():
                self._walk(path, keys)
            elif entry.is_file() and not entry.name.startswith(".tmp-"):
                keys.append(self._key_of(path))

    def _key_of(self, path: Path) -> str:
        return path.relative_to(self._root).as_posix()

    def _contained(self, key: str) -> Path:
        path = self._root.joinpath(*key.split("/")) if key else self._root
        self._check(path)
        return path

    def _check(self, path: Path) -> None:
        if not path.resolve().is_relative_to(self._root):
            raise AccessDenied("la ruta sale del workspace")


def _translate(exc: OSError) -> Exception:
    if isinstance(exc, PermissionError) or exc.errno == errno.ELOOP:
        return AccessDenied("acceso denegado")
    return StorageUnavailable("almacenamiento no disponible")


_DIR_FD_SUPPORTED = {
    os.open,
    os.mkdir,
    os.rename,
    os.link,
    os.unlink,
    os.stat,
} <= os.supports_dir_fd
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def _descend(parent: int, name: str) -> int:
    """Abre `name` bajo `parent`, creándolo si falta; un enlace se rechaza."""
    try:
        return _open_dir(parent, name)
    except FileNotFoundError:
        try:
            os.mkdir(name, 0o777, dir_fd=parent)
        except FileExistsError:
            pass
        return _open_dir(parent, name)


def _open_dir(parent: int, name: str) -> int:
    try:
        return os.open(name, _DIR_FLAGS, dir_fd=parent)
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR} and _is_link(parent, name):
            raise AccessDenied("la ruta sale del workspace") from exc
        raise


def _refuse_link(parent: int, name: str) -> None:
    if _is_link(parent, name):
        raise AccessDenied("la ruta sale del workspace")


def _is_link(parent: int, name: str) -> bool:
    try:
        return stat.S_ISLNK(os.stat(name, dir_fd=parent, follow_symlinks=False).st_mode)
    except OSError:
        return False


def _stage(parent: int, data: bytes) -> str:
    """Escribe `data` en un temporal bajo `parent`; devuelve su nombre."""
    name = f".tmp-{secrets.token_hex(8)}"
    fd = os.open(
        name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
        0o600,
        dir_fd=parent,
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        _discard(parent, name)
        raise
    return name


def _discard(parent: int, name: str) -> None:
    try:
        os.unlink(name, dir_fd=parent)
    except FileNotFoundError:
        pass


def _walk_root(root: Path, *, create: bool) -> int:
    """Abre la raíz (ya resuelta) componente a componente desde `/` sin seguir enlaces: ni una
    raíz ausente que aparezca como enlace antes de crearse puede anclar el almacén fuera."""
    fd = os.open("/", _DIR_FLAGS)
    try:
        for name in root.parts[1:]:
            child = _descend(fd, name) if create else _open_dir(fd, name)
            os.close(fd)
            fd = child
    except BaseException:
        os.close(fd)
        raise
    return fd
