"""Claves relativas normalizadas de un workspace."""

from ens_evidence.workspace.errors import InvalidKey


def normalize_key(key: str, *, allow_empty: bool = False) -> str:
    """Devuelve la clave con `/` como separador; rechaza absolutas, `..` y vacías."""
    if not isinstance(key, str) or "\\" in key or "\x00" in key:
        raise InvalidKey("clave no válida")
    if key.startswith("/") or (len(key) > 1 and key[1] == ":"):
        raise InvalidKey("la clave no puede ser absoluta")
    parts = [part for part in key.split("/") if part not in ("", ".")]
    if ".." in parts:
        raise InvalidKey("la clave no puede contener '..'")
    if not parts and not allow_empty:
        raise InvalidKey("la clave no puede estar vacía")
    return "/".join(parts)
