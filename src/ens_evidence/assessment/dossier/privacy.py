"""Política de texto exportable del expediente: un único sitio para escribir, leer y empaquetar.

Tres clases de cadena, según el campo que la contiene:
- huella sha256: opaca, no se revisa;
- mensaje de la herramienta (bloqueos, errores, motivos) y claves de ficheros del workspace: la
  herramienta los emite y pueden llevar fechas o números, así que solo se buscan credenciales;
- el resto es texto que escribe una persona: credenciales y datos personales.

`find_secrets` se aplica siempre sobre todo el documento. Los errores nombran la ruta y el tipo,
nunca el valor.
"""

import re
from collections.abc import Iterator

from ens_evidence.privacy_text import has_credential, text_problems
from ens_evidence.scope.secrets import find_secrets

_SHA256 = re.compile(r"[0-9a-f]{64}")
_INDEX = re.compile(r"\[\d+\]$")
TOOL_MESSAGE_FIELDS = frozenset(
    {
        "detail",
        "errors",
        "reasons",
        "problems",
        "unreadable",
        "state_errors",
        "validation_errors",
        "treatment_plan_errors",
        "pre_audit",
    }
)


def walk_texts(value: object, path: str = "") -> Iterator[tuple[str, str]]:
    """(ruta, texto) de cada cadena; las rutas de lista llevan índice."""
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, child in value.items():
            yield from walk_texts(child, f"{path}.{key}" if path else str(key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk_texts(child, f"{path}[{index}]")


def _is_tool_text(path: str) -> bool:
    """Un mensaje que emite la herramienta o la clave de un fichero: el campo, o el de una lista de
    mensajes (`errors[2]`)."""
    segments = [_INDEX.sub("", part) for part in path.split(".")]
    last = segments[-1]
    return last in TOOL_MESSAGE_FIELDS or last == "key" or last.endswith("_key")


def export_problems(where: str, value: object) -> list[str]:
    """Credenciales y datos personales del texto exportable bajo `where`; vacío si es seguro."""
    found: list[str] = []
    for path, text in walk_texts(value):
        if _SHA256.fullmatch(text):
            continue
        if _is_tool_text(path):
            problems = ["contiene una credencial"] if has_credential(text) else []
        else:
            problems = text_problems(text)[0]
        found += [f"{where}{path}: {problem}" for problem in problems]
    return found


def secret_problems(document: object, where: str = "instantánea.") -> list[str]:
    return [f"{where}{path}: parece un secreto" for path in find_secrets(document)]
