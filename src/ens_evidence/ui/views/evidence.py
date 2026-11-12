"""Evidencias: los manifiestos (nunca el contenido) y la subida de una evidencia manual."""

from datetime import UTC, datetime

from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import BaseRoute, Route

from ens_evidence.evidence import list_manifests
from ens_evidence.evidence.importer import import_manual
from ens_evidence.evidence.layout import is_evidence_id
from ens_evidence.incidents.privacy import check_declared_name
from ens_evidence.scope.inventory import SYSTEM_KEY
from ens_evidence.ui.audit import audit_write
from ens_evidence.ui.forms import (
    MAX_UPLOAD_BYTES,
    Form,
    FormInvalid,
    UploadTooLarge,
    first,
    read_upload,
)
from ens_evidence.ui.pages import error_page, identity_of, page_context, render
from ens_evidence.ui.store import UiStore
from ens_evidence.ui.views import format_instant
from ens_evidence.ui.wizard.base import read_yaml

BY_LABEL = "Quién la aporta"
MAX_MEGABYTES = MAX_UPLOAD_BYTES // (1024 * 1024)


def evidencias(request: Request) -> Response:
    store, now = request.app.state.store, request.app.state.clock()
    listing = list_manifests(store)
    uploaded = request.query_params.get("subida", "")
    return render(
        request,
        "views/evidencias.html",
        **page_context(request, "evidencias", "Evidencias"),
        manifests=[
            {
                "id": m.id,
                "source": m.source,
                "collected_at": format_instant(m.collected_at),
                "status": "EXPIRED" if m.valid_until and m.valid_until <= now else "CURRENT",
            }
            for m in sorted(listing.manifests, key=lambda m: m.collected_at, reverse=True)
        ],
        uploaded=uploaded if is_evidence_id(uploaded) else "",
    )


def _scope_assets(store: UiStore) -> list[str]:
    return [str(a) for a in read_yaml(store, SYSTEM_KEY).get("included_assets") or []]


def _form_page(
    request: Request,
    *,
    values: dict[str, str] | None = None,
    form_errors: list[str] | None = None,
    status_code: int = 200,
) -> Response:
    store: UiStore = request.app.state.store
    return render(
        request,
        "views/evidencias_nueva.html",
        status_code=status_code,
        **page_context(request, "evidencias", "Subir una evidencia"),
        assets=_scope_assets(store),
        max_megabytes=MAX_MEGABYTES,
        by_default=(values or {}).get("by", ""),
        values=values or {},
        form_errors=form_errors or [],
    )


def nueva(request: Request) -> Response:
    return _form_page(request)


def _collected_at(text: str, now: datetime) -> datetime | None:
    """La fecha opcional (AAAA-MM-DD) a medianoche UTC; `ValueError` si no vale o es futura."""
    if not text:
        return None
    collected = datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=UTC)
    if collected > now:
        raise ValueError(text)
    return collected


def _problems(fields: Form, content: bytes | None, assets: list[str], now: datetime) -> list[str]:
    problems = []
    if not content:
        problems.append("Adjunta un fichero que no esté vacío.")
    if first(fields, "asset_id") not in assets:
        problems.append("Elige uno de los activos del alcance.")
    problems.extend(check_declared_name(first(fields, "by"), BY_LABEL))
    try:
        _collected_at(first(fields, "collected_on"), now)
    except ValueError:
        problems.append("La fecha no es válida o es futura. Usa el formato AAAA-MM-DD.")
    return problems


async def subir(request: Request) -> Response:
    try:
        upload = await read_upload(request)
    except UploadTooLarge:
        return error_page(request, 413, f"El fichero supera el máximo de {MAX_MEGABYTES} MB.")
    except FormInvalid:
        return error_page(request, 400, "No se ha entendido el formulario.")
    now, email = request.app.state.clock(), identity_of(request).email
    store: UiStore = request.app.state.store.recording()
    fields = upload.fields
    echo = {k: v[0] for k, v in fields.items() if v}
    problems = _problems(fields, upload.content, _scope_assets(store), now)
    if problems or upload.content is None:
        return _form_page(request, values=echo, form_errors=problems, status_code=422)
    try:
        manifest = import_manual(
            store,
            upload.content,
            asset_id=first(fields, "asset_id"),
            submitted_by=first(fields, "by"),
            now=now,
            collected_at=_collected_at(first(fields, "collected_on"), now),
        )
    except ValidationError:
        return _form_page(
            request,
            values=echo,
            form_errors=["Los datos de la evidencia no son válidos. Revísalos."],
            status_code=422,
        )
    audit_write(identity=email, step="evidencias", action="subir", keys=store.written, at=now)
    return RedirectResponse(f"/evidencias?subida={manifest.id}", status_code=303)


EVIDENCE_ROUTES: list[BaseRoute] = [
    Route("/evidencias", evidencias, methods=["GET"]),
    Route("/evidencias/nueva", nueva, methods=["GET"]),
    Route("/evidencias/nueva", subir, methods=["POST"]),
]
