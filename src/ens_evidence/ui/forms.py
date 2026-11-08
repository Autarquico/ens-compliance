"""Lectura acotada de formularios: tope de cuerpo y de campos, sin ficheros."""

from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException
from starlette.requests import Request

MAX_BODY_BYTES = 256 * 1024
MAX_FIELDS = 600
FORM_TYPES = ("application/x-www-form-urlencoded", "multipart/form-data")

Form = dict[str, list[str]]


class FormTooLarge(Exception):
    """El cuerpo o el número de campos supera el tope."""


class FormInvalid(Exception):
    """El cuerpo no es un formulario legible."""


async def read_form(request: Request) -> Form:
    declared = request.headers.get("content-length")
    if declared is not None and (not declared.isdigit() or int(declared) > MAX_BODY_BYTES):
        raise FormTooLarge
    if not request.headers.get("content-type", "").lower().startswith(FORM_TYPES):
        raise FormInvalid
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise FormTooLarge
        chunks.append(chunk)
    body = b"".join(chunks)

    async def replay() -> dict:
        return {"type": "http.request", "body": body, "more_body": False}

    try:
        data = await Request(request.scope, replay).form(
            max_files=0, max_fields=MAX_FIELDS, max_part_size=MAX_BODY_BYTES
        )
    except MultiPartException:
        raise FormInvalid from None
    form: Form = {}
    for name, value in data.multi_items():
        if isinstance(value, UploadFile):
            raise FormInvalid
        form.setdefault(name, []).append(value)
    return form


def first(form: Form, name: str) -> str:
    """El primer valor del campo, sin espacios en los extremos; vacío si no vino."""
    values = form.get(name)
    return values[0].strip() if values else ""
