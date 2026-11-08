"""Almacén de la UI: lee todo el workspace, escribe solo bajo la lista blanca (ADR 0021)."""

from ens_evidence.workspace.keys import normalize_key
from ens_evidence.workspace.store import WorkspaceStore

WRITABLE_PREFIXES = ("scope", "controls", "risk", "org", "assessments")


class WriteNotAllowed(Exception):
    """La UI intentó escribir fuera de la lista blanca; no incluye la clave."""


class UiStore:
    def __init__(self, inner: WorkspaceStore) -> None:
        self._inner = inner
        self.written: list[str] = []

    def recording(self) -> "UiStore":
        """Una vista del mismo workspace que anota las claves que escribe (una por petición)."""
        return UiStore(self._inner)

    def read_text(self, key: str) -> str:
        return self._inner.read_text(key)

    def read_bytes(self, key: str) -> bytes:
        return self._inner.read_bytes(key)

    def exists(self, key: str) -> bool:
        return self._inner.exists(key)

    def list(self, prefix: str = "") -> list[str]:
        return self._inner.list(prefix)

    def write_text(self, key: str, text: str) -> None:
        self._inner.write_text(self._allowed_and_noted(key), text)

    def write_bytes(self, key: str, data: bytes) -> None:
        self._inner.write_bytes(self._allowed_and_noted(key), data)

    def create_bytes(self, key: str, data: bytes) -> None:
        self._inner.create_bytes(self._allowed_and_noted(key), data)

    def _allowed_and_noted(self, key: str) -> str:
        allowed = self._allowed(key)
        if allowed not in self.written:
            self.written.append(allowed)
        return allowed

    @staticmethod
    def _allowed(key: str) -> str:
        normalized = normalize_key(key)
        if normalized.split("/", 1)[0] not in WRITABLE_PREFIXES or "/" not in normalized:
            raise WriteNotAllowed("la UI no puede escribir en esa ruta")
        return normalized
