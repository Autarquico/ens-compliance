"""Almacén de solo lectura con memoria, para una operación del expediente.

Una generación, una reproducción o una comprobación leen el mismo workspace varias veces por
sección. Con este envoltorio cada clave se lee una vez (los bytes; el texto se deriva de ellos), así
que la operación ve una lectura coherente (sin que un cambio a mitad de operación afecte a unas
secciones y a otras no) y no paga las lecturas repetidas. Escribir es un error de programación: el
expediente solo lee.
"""

from collections.abc import Callable
from typing import TypeVar

from ens_evidence.workspace import NotText, WorkspaceStore

T = TypeVar("T")
_FAILED = object()


class ReadOnlyStore:
    def __init__(self, store: WorkspaceStore) -> None:
        self._store = store
        self._cache: dict[tuple[str, str], object] = {}

    def _once(self, call: str, key: str, read: Callable[[str], T]) -> T:
        slot = (call, key)
        if slot not in self._cache:
            try:
                self._cache[slot] = read(key)
            except Exception as error:  # se memoriza también el fallo: misma lectura, mismo error
                self._cache[slot] = (_FAILED, error)
        found = self._cache[slot]
        if isinstance(found, tuple) and len(found) == 2 and found[0] is _FAILED:
            raise found[1]
        return found  # type: ignore[return-value]

    def read_text(self, key: str) -> str:
        try:
            return self.read_bytes(key).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise NotText("el fichero no es texto UTF-8") from exc

    def read_bytes(self, key: str) -> bytes:
        return self._once("bytes", key, self._store.read_bytes)

    def exists(self, key: str) -> bool:
        return self._once("exists", key, self._store.exists)

    def list(self, prefix: str = "") -> list[str]:
        return list(self._once("list", prefix, self._store.list))

    def write_text(self, key: str, text: str) -> None:
        raise TypeError("el almacén de lectura del expediente no escribe")

    def write_bytes(self, key: str, data: bytes) -> None:
        raise TypeError("el almacén de lectura del expediente no escribe")

    def create_bytes(self, key: str, data: bytes) -> None:
        raise TypeError("el almacén de lectura del expediente no escribe")
