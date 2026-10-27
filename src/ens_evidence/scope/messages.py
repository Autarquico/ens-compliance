"""Mensajes de validación en español: fichero › id › campo: qué pasa y qué hacer."""

from typing import Any

from pydantic import ValidationError

SEPARATOR = " › "
_VALUE_ERROR_PREFIX = "Value error, "

_TEXTS = {
    "missing": "falta este campo; añádelo",
    "string_too_short": "no puede estar vacío; rellénalo",
    "extra_forbidden": "campo desconocido; revisa el nombre o bórralo",
    "bool_parsing": "debe ser true o false",
    "literal_error": "valor no admitido; usa uno de los valores permitidos",
    "datetime_parsing": "fecha y hora no válidas; usa el formato 2026-01-31T10:00:00+00:00",
    "datetime_from_date_parsing": "falta la hora; usa el formato 2026-01-31T10:00:00+00:00",
    "datetime_type": "fecha y hora no válidas; usa el formato 2026-01-31T10:00:00+00:00",
    "timezone_aware": "falta la zona horaria; usa el formato 2026-01-31T10:00:00+00:00",
}


def describe_validation(source: str, exc: ValidationError, raw: Any = None) -> list[str]:
    """Un mensaje por error; nunca incluye el valor recibido."""
    return [_describe(source, error, raw) for error in exc.errors(include_url=False)]


def _describe(source: str, error: Any, raw: Any) -> str:
    loc = list(error["loc"])
    owner = _owner_id(raw, loc)
    field = ".".join(str(part) for part in loc) or "<raíz>"
    parts = [source, *([owner] if owner else []), field]
    return f"{SEPARATOR.join(parts)}: {_text(error)}"


def _text(error: Any) -> str:
    kind = error["type"]
    if kind == "enum":
        return f"valor no admitido; usa uno de: {error.get('ctx', {}).get('expected', '')}"
    return _TEXTS.get(kind) or error["msg"].removeprefix(_VALUE_ERROR_PREFIX)


def _owner_id(raw: Any, loc: list[Any]) -> str | None:
    if len(loc) < 2 or not isinstance(raw, dict) or not isinstance(loc[1], int):
        return None
    items = raw.get(loc[0])
    if isinstance(items, list) and loc[1] < len(items) and isinstance(items[loc[1]], dict):
        item_id = items[loc[1]].get("id")
        return item_id if isinstance(item_id, str) and item_id else None
    return None
