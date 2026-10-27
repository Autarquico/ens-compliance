"""Contrato de almacenamiento del workspace."""

from typing import Protocol


class WorkspaceStore(Protocol):
    """Ficheros de texto de una organización, direccionados por clave relativa.

    `create_bytes` es una creación exclusiva: lanza `AlreadyExists` si la clave
    existe y `ConditionalWriteUnsupported` si el backend no puede garantizarlo.
    `list(prefix)` devuelve, ordenadas, las claves bajo el directorio `prefix`
    (`""` lista todo el workspace).
    """

    def read_text(self, key: str) -> str: ...

    def write_text(self, key: str, text: str) -> None: ...

    def read_bytes(self, key: str) -> bytes: ...

    def write_bytes(self, key: str, data: bytes) -> None: ...

    def create_bytes(self, key: str, data: bytes) -> None: ...

    def exists(self, key: str) -> bool: ...

    def list(self, prefix: str = "") -> list[str]: ...
