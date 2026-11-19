"""Almacén de la UI: lee todo el workspace, escribe solo bajo la lista blanca (ADR 0021)."""

from collections.abc import Iterator
from contextlib import contextmanager, nullcontext

from ens_evidence.workspace.keys import normalize_key
from ens_evidence.workspace.store import WorkspaceStore

# Prefijos exactos: `evidence/` entero no, porque ahí escribe también `app` (checks, copias…).
WRITABLE_PREFIXES = (
    "scope",
    "controls",
    "risk",
    "org",
    "incidents",
    "connectors",
    "evidence/assessments",
    "evidence/key-custodians",
    "evidence/raw",
    "evidence/manifests",
    "evidence/preaudits/requests",
    "evidence/preaudits/dispositions",
    "evidence/collect-requests",
)


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

    def read_bytes(self, key: str, limit: int | None = None) -> bytes:
        return self._inner.read_bytes(key, limit)

    def exists(self, key: str) -> bool:
        return self._inner.exists(key)

    def list(self, prefix: str = "") -> list[str]:
        return self._inner.list(prefix)

    def write_text(self, key: str, text: str) -> None:
        allowed = self._allowed(key)
        self._inner.write_text(allowed, text)
        self._note(allowed)

    def write_bytes(self, key: str, data: bytes) -> None:
        allowed = self._allowed(key)
        self._inner.write_bytes(allowed, data)
        self._note(allowed)

    def create_bytes(self, key: str, data: bytes) -> None:
        allowed = self._allowed(key)
        self._inner.create_bytes(allowed, data)
        self._note(allowed)

    @contextmanager
    def exclusive_lock(self, key: str) -> Iterator[None]:
        """Lock exclusivo bajo la lista blanca; sin esa capacidad en el almacén no hay lock."""
        opener = getattr(self._inner, "exclusive_lock", None)
        allowed = self._allowed(key)
        with opener(allowed) if opener else nullcontext():
            yield

    def _note(self, allowed: str) -> None:
        if allowed not in self.written:
            self.written.append(allowed)

    @staticmethod
    def _allowed(key: str) -> str:
        normalized = normalize_key(key)
        if not any(normalized.startswith(f"{prefix}/") for prefix in WRITABLE_PREFIXES):
            raise WriteNotAllowed("la UI no puede escribir en esa ruta")
        return normalized
