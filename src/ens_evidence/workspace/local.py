"""Backend de carpeta local con contención verificada."""

import os
import tempfile
from collections.abc import MutableSequence
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

    def read_text(self, key: str) -> str:
        try:
            return self.read_bytes(key).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise NotText("el fichero no es texto UTF-8") from exc

    def read_bytes(self, key: str) -> bytes:
        path = self._contained(normalize_key(key))
        try:
            return path.read_bytes()
        except (FileNotFoundError, IsADirectoryError, NotADirectoryError) as exc:
            raise NotFound("clave no encontrada") from exc
        except OSError as exc:
            raise _translate(exc) from exc

    def write_text(self, key: str, text: str) -> None:
        self.write_bytes(key, text.encode("utf-8"))

    def write_bytes(self, key: str, data: bytes) -> None:
        tmp_name = None
        try:
            path, tmp_name = self._stage(key, data)
            os.replace(tmp_name, path)
        except OSError as exc:
            _discard(tmp_name)
            raise _translate(exc) from exc

    def create_bytes(self, key: str, data: bytes) -> None:
        tmp_name = None
        try:
            path, tmp_name = self._stage(key, data)
            os.link(tmp_name, path)
        except FileExistsError as exc:
            raise AlreadyExists("la clave ya existe") from exc
        except OSError as exc:
            raise _translate(exc) from exc
        finally:
            _discard(tmp_name)

    def _stage(self, key: str, data: bytes) -> tuple[Path, str]:
        """Escribe `data` en un temporal junto al destino; devuelve (destino, temporal)."""
        path = self._contained(normalize_key(key))
        path.parent.mkdir(parents=True, exist_ok=True)
        path = self._contained(normalize_key(key))
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            _discard(tmp_name)
            raise
        return path, tmp_name

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
    if isinstance(exc, PermissionError):
        return AccessDenied("acceso denegado")
    return StorageUnavailable("almacenamiento no disponible")


def _discard(tmp_name: str | None) -> None:
    if tmp_name is not None:
        Path(tmp_name).unlink(missing_ok=True)
