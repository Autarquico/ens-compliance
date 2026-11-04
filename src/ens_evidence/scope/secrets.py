"""Detector heurístico de secretos. Informa la ruta del campo, nunca el valor."""

import re
from typing import Any

_VALUE_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY(?: BLOCK)?-----"),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    re.compile(r"[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@"),
    re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}"),
    re.compile(r"\b[rs]k_(?:live|test)_[0-9A-Za-z]{16,}"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}"),
    re.compile(r"hooks\.slack\.com/services/T[0-9A-Z]+/B[0-9A-Z]+/[0-9A-Za-z]{16,}"),
    # Un token tras «Bearer», con algún dígito: «Bearer authentication» en prosa no lo es.
    re.compile(r"\b[Bb]earer\s+(?=[A-Za-z0-9._~+/-]*\d)[A-Za-z0-9._~+/-]{16,}=*"),
)
_SECRET_KEY = re.compile(r"(password|passwd|secret|token|api[_-]?key|private[_-]?key)", re.I)


def find_secrets(
    data: Any, path: str = "", *, by_key_name: bool = True, in_keys: bool = False
) -> list[str]:
    """Rutas de los valores que parecen secretos.

    `by_key_name=False` ignora la heurística por nombre de clave (marcaría `secret_scanning:
    enabled`). `in_keys=True` mira también si una clave tiene forma de secreto (la ruta, entonces,
    lo contiene: quien la cite debe ocultarla)."""
    found: list[str] = []
    if isinstance(data, dict):
        for key, value in data.items():
            child = f"{path}.{key}" if path else str(key)
            if in_keys and _looks_like_secret(str(key)):
                found.append(child)
            if (
                by_key_name
                and _SECRET_KEY.search(str(key))
                and isinstance(value, str)
                and value.strip()
            ):
                found.append(child)
            else:
                found.extend(find_secrets(value, child, by_key_name=by_key_name, in_keys=in_keys))
    elif isinstance(data, list):
        for index, value in enumerate(data):
            found.extend(
                find_secrets(value, f"{path}[{index}]", by_key_name=by_key_name, in_keys=in_keys)
            )
    elif isinstance(data, str) and _looks_like_secret(data):
        found.append(path)
    return found


def _looks_like_secret(text: str) -> bool:
    return any(pattern.search(text) for pattern in _VALUE_PATTERNS)
