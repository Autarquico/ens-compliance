"""Backend de carpeta local con contención verificada."""

import os
import tempfile
from collections.abc import MutableSequence
from pathlib import Path

from ens_evidence.workspace.errors import AccessDenied, NotFound, NotText, StorageUnavailable
from ens_evidence.workspace.keys import normalize_key


class LocalStore:
    def __init__(self, root: Path) -> None:
        self._root = Path(root).resolve()

    def read_text(self, key: str) -> str:
        path = self._contained(normalize_key(key))
        try:
            return path.read_text(encoding="utf-8")
        except (FileNotFoundError, IsADirectoryError, NotADirectoryError) as exc:
            raise NotFound("clave no encontrada") from exc
        except UnicodeDecodeError as exc:
            raise NotText("el fichero no es texto UTF-8") from exc
        except OSError as exc:
            raise _translate(exc) from exc

    def write_text(self, key: str, text: str) -> None:
        path = self._contained(normalize_key(key))
        tmp_name = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path = self._contained(normalize_key(key))
            fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, path)
        except OSError as exc:
            if tmp_name is not None:
                Path(tmp_name).unlink(missing_ok=True)
            raise _translate(exc) from exc

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
