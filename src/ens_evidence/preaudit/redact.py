"""Redacción del payload de la pre-auditoría (plan pre-audit, «Cambios r2», punto 7).

`privacy_text` y `find_secrets` detectan; aquí se sustituye. Después se vuelven a pasar los
mismos detectores sobre el resultado: si queda algo detectable, no se envía. Los errores nombran
la ruta, nunca el valor.
"""

import re
from collections.abc import Callable, Collection, Iterator, Mapping
from typing import Any

from ens_evidence.matrix.messages import role_label
from ens_evidence.models.enums import Role
from ens_evidence.privacy_text import (
    credential_spans,
    dni_nie_spans,
    email_spans,
    iban_spans,
    ip_spans,
    phone_spans,
    text_problems,
)
from ens_evidence.scope.secrets import find_secrets

SECRET = "[REDACTADO:secreto]"
IP = "[IP]"
PERSON = "[PERSONA]"
_PEM_BEGIN = "-----BEGIN"
_PEM_END = "-----END"
_TOKEN = re.compile(r"\S+")

Designated = Mapping[str, Collection[Role]]
"""Nombre declarado → roles que ocupa esa persona (vacío: se sustituye por `[PERSONA]`)."""

# Orden: la IP antes que el teléfono (una IP no es un teléfono, pero una lista de IP con texto
# alrededor podría parecerlo una vez rota).
_IDENTIFIERS: tuple[tuple[str, Callable[[str], list[tuple[int, int]]]], ...] = (
    (SECRET, credential_spans),
    ("[REDACTADO:correo]", email_spans),
    ("[REDACTADO:iban]", iban_spans),
    ("[REDACTADO:dni-nie]", dni_nie_spans),
    (IP, ip_spans),
    ("[REDACTADO:telefono]", phone_spans),
)


class RedactionLeak(ValueError):
    """Tras redactar sigue habiendo algo detectable; `problems` cita rutas, nunca valores."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


def redact_text(text: str, designated: Designated) -> str:
    text = _redact_secrets(text)
    for mark, spans in _IDENTIFIERS:
        text = _replace(text, spans(text), mark)
    return _redact_names(text, designated)


def redact_fields(data: Any, fields: Collection[str], designated: Designated) -> Any:
    """Copia de `data` con los textos de las claves `fields` redactados (cadenas o listas)."""
    if isinstance(data, dict):
        return {
            key: _redact_value(value, designated)
            if key in fields
            else redact_fields(value, fields, designated)
            for key, value in data.items()
        }
    if isinstance(data, list):
        return [redact_fields(item, fields, designated) for item in data]
    return data


def leaks(data: Any, fields: Collection[str], designated: Designated) -> list[str]:
    """Lo que los detectores aún encuentran: secretos en todo `data`; datos personales, IP y
    nombres designados en los textos de `fields`. Una línea por ruta y tipo, sin el valor."""
    problems = [f"{path}: parece un secreto" for path in find_secrets(data)]
    names = _names_pattern(designated)
    for path, text in _texts(data, fields):
        errors, warnings = text_problems(text)
        problems += [f"{path}: {problem}" for problem in errors + warnings]
        if names and names.search(text):
            problems.append(f"{path}: contiene el nombre de una persona designada")
    return problems


def require_clean(data: Any, fields: Collection[str], designated: Designated) -> None:
    if problems := leaks(data, fields, designated):
        raise RedactionLeak(problems)


def _redact_value(value: Any, designated: Designated) -> Any:
    if isinstance(value, str):
        return redact_text(value, designated)
    if isinstance(value, list):
        return [_redact_value(item, designated) for item in value]
    return value


def _texts(data: Any, fields: Collection[str], path: str = "") -> Iterator[tuple[str, str]]:
    """(ruta, texto) de las claves `fields`, con la notación de rutas de `find_secrets`."""
    if isinstance(data, dict):
        for key, value in data.items():
            child = f"{path}.{key}" if path else str(key)
            if key in fields:
                yield from _strings(value, child)
            else:
                yield from _texts(value, fields, child)
    elif isinstance(data, list):
        for index, item in enumerate(data):
            yield from _texts(item, fields, f"{path}[{index}]")


def _strings(value: Any, path: str) -> Iterator[tuple[str, str]]:
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _strings(item, f"{path}[{index}]")


def _redact_secrets(text: str) -> str:
    """Por línea: se tapan los tokens que parecen secretos; si la línea sigue pareciéndolo, la
    línea entera. Un bloque PEM se tapa hasta su `-----END`."""
    out: list[str] = []
    in_pem = False
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        ending = line[len(body) :]
        if in_pem:
            in_pem = _PEM_END not in body
            out.append(SECRET + ending)
            continue
        if not find_secrets(body):
            out.append(line)
            continue
        tokens = _TOKEN.sub(lambda m: SECRET if find_secrets(m.group()) else m.group(), body)
        if find_secrets(tokens):
            in_pem = _PEM_BEGIN in body and _PEM_END not in body.split(_PEM_BEGIN, 1)[1]
            tokens = SECRET
        out.append(tokens + ending)
    return "".join(out)


def _replace(text: str, spans: list[tuple[int, int]], mark: str) -> str:
    for start, end in sorted(_merge(spans), reverse=True):
        text = text[:start] + mark + text[end:]
    return text


def _merge(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def _variants(designated: Designated) -> dict[str, set[Role]]:
    """Formas de citar a cada persona → sus roles: el nombre completo y, si tiene tres palabras
    o más, nombre y primer apellido («Lucía Navarro Gil» → «Lucía Navarro»)."""
    variants: dict[str, set[Role]] = {}
    for name, held in designated.items():
        words = name.split()
        for variant in {" ".join(words), " ".join(words[:2]) if len(words) >= 3 else ""} - {""}:
            variants.setdefault(variant.casefold(), set()).update(held)
    return variants


def _names_pattern(designated: Designated) -> re.Pattern[str] | None:
    """Las variantes, la más larga primero, sin distinguir mayúsculas ni espacios."""
    variants = sorted(_variants(designated), key=len, reverse=True)
    if not variants:
        return None
    alternatives = "|".join(r"\s+".join(map(re.escape, v.split())) for v in variants)
    return re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)", re.IGNORECASE)


def _redact_names(text: str, designated: Designated) -> str:
    pattern = _names_pattern(designated)
    if pattern is None:
        return text
    variants = _variants(designated)
    return pattern.sub(lambda m: _label(variants[" ".join(m.group().split()).casefold()]), text)


def _label(roles: set[Role]) -> str:
    if not roles:
        return PERSON
    return "[" + " / ".join(sorted(role_label(role) for role in roles)) + "]"
