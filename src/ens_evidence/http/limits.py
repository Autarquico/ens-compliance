"""Lectura acotada del cuerpo: el tamaño se limita antes de interpretar nada."""

import json
from typing import Any

from starlette.requests import Request

MAX_JSON_DEPTH = 32
JSON_CONTENT_TYPE = "application/json"


class Rejected(Exception):
    """Petición ilegible o fuera de límites; `code` es estable y no incluye datos del cuerpo."""

    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code


async def read_json_body(
    request: Request, *, max_bytes: int, max_depth: int = MAX_JSON_DEPTH
) -> Any:
    _require_identity_encoding(request)
    _require_json_content_type(request)
    _reject_declared_oversize(request, max_bytes)
    raw = await _read_capped(request, max_bytes)
    value = _parse(raw)
    if _depth(value) > max_depth:
        raise Rejected(400, "too_deep")
    return value


def _require_identity_encoding(request: Request) -> None:
    encoding = request.headers.get("content-encoding", "identity").strip().lower()
    if encoding not in ("", "identity"):
        raise Rejected(415, "unsupported_encoding")


def _require_json_content_type(request: Request) -> None:
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != JSON_CONTENT_TYPE:
        raise Rejected(415, "unsupported_media_type")


def _reject_declared_oversize(request: Request, max_bytes: int) -> None:
    declared = request.headers.get("content-length")
    if declared is None:
        return
    if not declared.isascii() or not declared.isdigit():
        raise Rejected(400, "bad_content_length")
    if int(declared) > max_bytes:
        raise Rejected(413, "too_large")


async def _read_capped(request: Request, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > max_bytes:
            raise Rejected(413, "too_large")
        chunks.append(chunk)
    return b"".join(chunks)


def _reject_constant(_name: str) -> Any:
    raise ValueError("constante no válida")


def _parse(raw: bytes) -> Any:
    try:
        return json.loads(raw, parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        raise Rejected(400, "invalid_json") from None


def _depth(value: Any) -> int:
    deepest = 0
    pending = [(value, 1)]
    while pending:
        current, level = pending.pop()
        if isinstance(current, dict):
            children = list(current.values())
        elif isinstance(current, list):
            children = current
        else:
            continue
        deepest = max(deepest, level)
        pending.extend((child, level + 1) for child in children)
    return deepest
