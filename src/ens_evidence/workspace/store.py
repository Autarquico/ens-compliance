"""Contrato de almacenamiento del workspace."""

from typing import Protocol


class WorkspaceStore(Protocol):
    """Ficheros de texto de una organización, direccionados por clave relativa.

    `list(prefix)` devuelve, ordenadas, las claves bajo el directorio `prefix`
    (`""` lista todo el workspace).
    """

    def read_text(self, key: str) -> str: ...

    def write_text(self, key: str, text: str) -> None: ...

    def exists(self, key: str) -> bool: ...

    def list(self, prefix: str = "") -> list[str]: ...
