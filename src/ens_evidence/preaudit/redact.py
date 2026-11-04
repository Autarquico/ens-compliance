"""Redacción del payload de la pre-auditoría (plan pre-audit, «Cambios r2», punto 7).

`privacy_text` y `find_secrets` detectan; aquí se sustituye. Además, en los textos (evidencias de
hasta 64 KiB, logs, JSON de collectors) se buscan secretos que esos detectores no ven: codificados
en base64 o en URL, partidos entre dos líneas, una clave secreta de AWS sin etiqueta y tokens
largos de aspecto aleatorio. Después se vuelven a pasar todos sobre el resultado: si queda algo
detectable, no se envía. Los errores nombran la ruta, nunca el valor.

La detección es por patrones: no sustituye a leer el payload antes de consentir.

Los nombres designados se buscan sin distinguir mayúsculas, tildes ni caracteres invisibles, en
sus formas «Nombre Apellidos», «Nombre Apellido» y «Apellidos, Nombre». El nombre de pila suelto
no se tapa: taparía cualquier palabra que coincida con él.
"""

import base64
import binascii
import math
import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Collection, Iterator, Mapping
from typing import Any
from urllib.parse import unquote

from ens_evidence.matrix.messages import role_label
from ens_evidence.models.enums import Role
from ens_evidence.privacy_text import (
    credential_spans,
    dni_nie_spans,
    email_spans,
    has_credential,
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
# Tramos que podrían ser un secreto codificado o aleatorio (alfabetos base64, base64url y URL).
_CANDIDATE = re.compile(r"[A-Za-z0-9+/_%=-]{24,}")
_BASE64 = re.compile(r"[A-Za-z0-9+/_-]+={0,2}")
_AWS_SECRET = re.compile(r"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/]{40}(?![A-Za-z0-9+/=])")
_RANDOM_MIN_LENGTH = 32
_RANDOM_MIN_ENTROPY = 4.0
# Fracción de cambios de clase (mayúscula, minúscula, dígito, otro) entre caracteres vecinos: un
# token aleatorio base64 ronda 0,6; rutas, identificadores y palabras unidas, 0,25-0,40.
_RANDOM_MIN_CLASS_CHANGES = 0.45

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
        if _hidden_secret_spans(text) or _split_secret_lines(text.splitlines()):
            problems.append(f"{path}: parece un secreto codificado, partido o aleatorio")
        if names and names[0].search(_fold(text)[0]):
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
    línea entera. Un bloque PEM se tapa hasta su `-----END`, y las dos líneas entre las que se
    parte un secreto, enteras. Después, los secretos codificados o aleatorios."""
    lines = text.splitlines(keepends=True)
    split = _split_secret_lines([line.rstrip("\r\n") for line in lines])
    out: list[str] = []
    in_pem = False
    for index, line in enumerate(lines):
        body = line.rstrip("\r\n")
        ending = line[len(body) :]
        if in_pem or index in split:
            in_pem = in_pem and _PEM_END not in body
            out.append(SECRET + ending)
            continue
        if find_secrets(body):
            body = _TOKEN.sub(lambda m: SECRET if find_secrets(m.group()) else m.group(), body)
            if find_secrets(body):
                original = line.rstrip("\r\n")
                in_pem = _PEM_BEGIN in original and _PEM_END not in original.split(_PEM_BEGIN, 1)[1]
                body = SECRET
        out.append(_replace(body, _hidden_secret_spans(body), SECRET) + ending)
    return "".join(out)


def _split_secret_lines(lines: list[str]) -> set[int]:
    """Índices de las líneas entre cuyo final y el principio de la siguiente hay un secreto que
    ninguna de las dos muestra por separado."""
    found: set[int] = set()
    for index in range(len(lines) - 1):
        tail = lines[index].rsplit(maxsplit=1)[-1:]
        head = lines[index + 1].split(maxsplit=1)[:1]
        if not tail or not head or find_secrets(tail[0]) or find_secrets(head[0]):
            continue
        if find_secrets(tail[0] + head[0]):
            found |= {index, index + 1}
    return found


def _hidden_secret_spans(text: str) -> list[tuple[int, int]]:
    """Tramos con un secreto que los detectores compartidos no ven tal cual."""
    spans = [m.span() for m in _AWS_SECRET.finditer(text) if _mixed(m.group())]
    spans += [m.span() for m in _CANDIDATE.finditer(text) if _hidden_secret(m.group())]
    return spans


def _hidden_secret(token: str) -> bool:
    if "%" in token and _reveals_secret(unquote(token)):
        return True
    return _reveals_secret(_base64_text(token)) or _looks_random(token)


def _reveals_secret(text: str | None) -> bool:
    return text is not None and bool(find_secrets(text) or has_credential(text))


def _base64_text(token: str) -> str | None:
    """El texto que codifica `token` en base64 o base64url, si es UTF-8 imprimible."""
    if not _BASE64.fullmatch(token):
        return None
    padded = token.rstrip("=") + "=" * (-len(token.rstrip("=")) % 4)
    try:
        decoded = base64.b64decode(padded.replace("-", "+").replace("_", "/"), validate=True)
        text = decoded.decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None
    return text if text.isprintable() else None


def _looks_random(token: str) -> bool:
    core = token.strip("=")
    if len(core) < _RANDOM_MIN_LENGTH or not _mixed(core):
        return False
    counts = Counter(core)
    entropy = -sum(n / len(core) * math.log2(n / len(core)) for n in counts.values())
    changes = sum(_class(a) != _class(b) for a, b in zip(core, core[1:], strict=False))
    return entropy >= _RANDOM_MIN_ENTROPY and changes / (len(core) - 1) >= _RANDOM_MIN_CLASS_CHANGES


def _mixed(token: str) -> bool:
    """Con mayúsculas, minúsculas y dígitos: descarta hex, UUID y palabras."""
    return (
        any(c.isupper() for c in token)
        and any(c.islower() for c in token)
        and any(c.isdigit() for c in token)
    )


def _class(char: str) -> str:
    if char.isupper():
        return "A"
    if char.islower():
        return "a"
    return "0" if char.isdigit() else "."


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


def _fold(text: str) -> tuple[str, list[int]]:
    """`text` sin tildes ni caracteres de formato (anchura cero…) y en minúsculas, con el índice
    en `text` de cada carácter del resultado."""
    folded: list[str] = []
    origin: list[int] = []
    for index, char in enumerate(text):
        if unicodedata.category(char) == "Cf":
            continue
        for part in unicodedata.normalize("NFKD", char):
            if unicodedata.combining(part):
                continue
            for lower in part.casefold():
                folded.append(lower)
                origin.append(index)
    return "".join(folded), origin


def _variants(designated: Designated) -> dict[tuple[str, ...], set[Role]]:
    """Formas de citar a cada persona (palabras plegadas; "," separa «Apellidos, Nombre») → sus
    roles: el nombre completo, nombre y primer apellido si tiene tres palabras o más («Lucía
    Navarro Gil» → «Lucía Navarro») y apellidos, nombre («Navarro Gil, Lucía»)."""
    variants: dict[tuple[str, ...], set[Role]] = {}
    for name, held in designated.items():
        words = tuple(_fold(name)[0].split())
        forms = {words}
        if len(words) >= 3:
            forms.add(words[:2])
        if len(words) >= 2:
            forms.add((*words[1:], ",", words[0]))
        for form in forms:
            variants.setdefault(form, set()).update(held)
    return variants


def _names_pattern(
    designated: Designated,
) -> tuple[re.Pattern[str], list[set[Role]]] | None:
    """Las variantes, la más larga primero, sobre texto plegado (`_fold`) y sin distinguir
    espacios; el grupo `v<i>` que casa indica los roles `[i]`."""
    variants = sorted(_variants(designated).items(), key=lambda item: len(item[0]), reverse=True)
    if not variants:
        return None
    alternatives = "|".join(
        f"(?P<v{i}>{_form_pattern(form)})" for i, (form, _) in enumerate(variants)
    )
    pattern = re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)")
    return pattern, [roles for _, roles in variants]


def _form_pattern(form: tuple[str, ...]) -> str:
    parts = " ".join(form).split(" , ")
    return r"\s*,\s*".join(r"\s+".join(map(re.escape, part.split())) for part in parts)


def _redact_names(text: str, designated: Designated) -> str:
    names = _names_pattern(designated)
    if names is None:
        return text
    pattern, roles = names
    folded, origin = _fold(text)
    for match in reversed(list(pattern.finditer(folded))):
        start, end = origin[match.start()], origin[match.end() - 1] + 1
        label = _label(roles[int(match.lastgroup[1:])])
        text = text[:start] + label + text[end:]
    return text


def _label(roles: set[Role]) -> str:
    if not roles:
        return PERSON
    return "[" + " / ".join(sorted(role_label(role) for role in roles)) + "]"
