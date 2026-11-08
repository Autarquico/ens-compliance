"""Vistas de solo lectura y utilidades comunes de las páginas."""

from datetime import UTC, datetime

NO_DATE = "—"


def format_instant(value: datetime | None) -> str:
    if value is None:
        return NO_DATE
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
