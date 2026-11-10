"""Herramientas: asistente del kit de claves (OpenBao) y propuesta en la matriz.

Como las copias, la UI no llama a OpenBao ni escribe en el workspace. El paquete no lleva
secretos: las partes de desbloqueo las genera la terminal del servidor y nunca se teclean aquí.
"""

from typing import Any

from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import BaseRoute, Route

from ens_evidence.kit.keystore import KeystoreInputs, render_bundle
from ens_evidence.scope.inventory import load_assets
from ens_evidence.scope.models import AssetType
from ens_evidence.ui.audit import audit_write
from ens_evidence.ui.forms import Form, FormInvalid, FormTooLarge, first, read_form
from ens_evidence.ui.pages import error_page, identity_of, page_context, render
from ens_evidence.ui.store import UiStore
from ens_evidence.ui.views.tools import KEYSTORE_CONNECTOR, KEYSTORE_MEASURES, kit_state

STEP = "claves"
ENABLE_COMMAND = "deploy/box.sh kit enable claves <paquete>"
INIT_COMMAND = "deploy/box.sh kit claves init"
INT_FIELDS = ("key_shares", "key_threshold", "max_key_age_days")
DEFAULTS = {"key_shares": "5", "key_threshold": "3", "max_key_age_days": "365"}
RANGE_TEXT = {
    "key_shares": "N debe estar entre 3 y 10.",
    "key_threshold": "M debe ser al menos 2.",
    "max_key_age_days": "Debe ser mayor que 0.",
}
GENERIC = "Revisa este campo."


def keystore_offer(store: UiStore, measure_id: str) -> dict[str, Any] | None:
    """Propuesta del kit para op.exp.10, mientras no haya evidencia de su almacén de claves."""
    if measure_id not in KEYSTORE_MEASURES or (
        kit_state(store, KEYSTORE_CONNECTOR)["key"] != "nodata"
    ):
        return None
    return {
        "name": "claves",
        "cta": "Preparar las claves",
        "url": "/herramientas/claves",
        "covers": (
            "Custodia cifrada de claves y secretos, con auditoría de accesos, control de acceso "
            "por políticas, inventario de claves, rotación según la política que fijes y "
            "desbloqueo por quórum de custodios. Es una parte de op.exp.10.1; en cada recogida "
            "se comprueba que el almacén está desbloqueado, auditado y con las claves rotadas."
        ),
        "not_covers": (
            "No cubre op.exp.10.2 ni op.exp.10.3: OpenBao vive en el mismo servidor que la "
            "explotación, así que no hay aislamiento de medios. Tampoco la R1 (algoritmos y "
            "parámetros autorizados por el CCN): la valora una persona con el inventario de "
            "tipos de clave. No cubre las claves que queden fuera de OpenBao, ni decide que la "
            "medida esté cubierta: eso lo valora una persona."
        ),
    }


def _application_assets(store: UiStore) -> list[str]:
    assets, _ = load_assets(store)
    if assets is None:
        return []
    return sorted(a.id for a in assets.assets if a.type is AssetType.APPLICATION)


def _page(
    request: Request,
    *,
    values: dict[str, Any] | None = None,
    errors: dict[str, list[str]] | None = None,
    form_errors: list[str] | None = None,
    status_code: int = 200,
) -> Response:
    return render(
        request,
        "views/herramientas_claves.html",
        status_code=status_code,
        **page_context(request, "herramientas", "Claves"),
        values=DEFAULTS | (values or {}),
        errors=errors or {},
        form_errors=form_errors or [],
        application_assets=_application_assets(request.app.state.store),
        enable_command=ENABLE_COMMAND,
        init_command=INIT_COMMAND,
    )


def asistente(request: Request) -> Response:
    return _page(request)


def _echo(form: Form) -> dict[str, str]:
    return {name: first(form, name) for name in ("asset_id", *INT_FIELDS)}


def _inputs(form: Form, assets: list[str]) -> tuple[KeystoreInputs | None, dict, list[str]]:
    fields: dict[str, list[str]] = {}
    general: list[str] = []
    numbers: dict[str, int] = {}
    for name in INT_FIELDS:
        text = first(form, name)
        if text.isascii() and text.isdigit():
            numbers[name] = int(text)
        else:
            fields[name] = ["Escribe un número entero, sin signo ni decimales."]
    asset_id = first(form, "asset_id")
    if asset_id not in assets:
        fields["asset_id"] = [
            "Elige un activo de tipo aplicación del inventario; si no hay, añádelo antes."
        ]
    if fields:
        return None, fields, general
    try:
        return KeystoreInputs(asset_id=asset_id, **numbers), fields, general
    except ValidationError as exc:
        for item in exc.errors(include_input=False, include_url=False):
            name = str(item["loc"][-1]) if item["loc"] else None
            if item["type"] == "value_error":
                fields.setdefault("key_threshold", []).append("M no puede ser mayor que N.")
            elif name in RANGE_TEXT:
                fields.setdefault(name, []).append(RANGE_TEXT[name])
            else:
                general.append(GENERIC)
        return None, fields, general


async def generar(request: Request) -> Response:
    try:
        form = await read_form(request)
    except FormTooLarge:
        return error_page(request, 413, "El formulario es demasiado grande.")
    except FormInvalid:
        return error_page(request, 400, "No se ha entendido el formulario.")
    now = request.app.state.clock()
    inputs, fields, general = _inputs(form, _application_assets(request.app.state.store))
    if inputs is None:
        return _page(
            request,
            values=_echo(form),
            errors=fields,
            form_errors=general or ["No se ha generado el paquete. Revisa los campos marcados."],
            status_code=422,
        )
    audit_write(identity=identity_of(request).email, step=STEP, action="paquete", keys=[], at=now)
    return Response(
        render_bundle(inputs, now),
        media_type="application/gzip",
        headers={
            "Content-Disposition": 'attachment; filename="claves.tar.gz"',
            "Cache-Control": "no-store",
        },
    )


KEYSTORE_ROUTES: list[BaseRoute] = [
    Route("/herramientas/claves", asistente, methods=["GET"]),
    Route("/herramientas/claves", generar, methods=["POST"]),
]
