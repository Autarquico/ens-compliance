"""Herramientas: asistente del kit de contraseñas (Vaultwarden).

Como las copias y las claves, la UI no llama a Vaultwarden ni escribe en el workspace. El paquete
solo lleva la configuración del conector (sin secretos); el nombre de host no va en él: se pone en
`deploy/ui.env`.
"""

from typing import Any

from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import BaseRoute, Route

from ens_evidence.kit.passwords_setup import PasswordsInputs, render_bundle
from ens_evidence.scope.inventory import load_assets
from ens_evidence.scope.models import AssetType
from ens_evidence.ui.audit import audit_write
from ens_evidence.ui.forms import Form, FormInvalid, FormTooLarge, first, read_form
from ens_evidence.ui.pages import error_page, identity_of, page_context, render
from ens_evidence.ui.store import UiStore

STEP = "contrasenas"
ENABLE_COMMAND = "deploy/box.sh kit enable contrasenas <paquete>"
HOST_SETTING = "ENS_CONTRASENAS_HOST"
SETUP_COMMAND = "deploy/box.sh kit contrasenas alta"
CLOSE_COMMAND = "deploy/box.sh kit contrasenas cerrar-alta"
FIELDS = ("asset_id", "inactive_days", "host")
FIELD_TEXT = {
    "inactive_days": "Escribe un número entero de días mayor que 0, sin signo ni decimales.",
    "host": "Escribe un nombre de dominio (contrasenas.example.com), sin https://, puerto ni ruta.",
}
GENERIC = "Revisa este campo."


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
        "views/herramientas_contrasenas.html",
        status_code=status_code,
        **page_context(request, "herramientas", "Contraseñas"),
        values=values or {},
        errors=errors or {},
        form_errors=form_errors or [],
        application_assets=_application_assets(request.app.state.store),
        enable_command=ENABLE_COMMAND,
        host_setting=HOST_SETTING,
        setup_command=SETUP_COMMAND,
        close_command=CLOSE_COMMAND,
    )


def asistente(request: Request) -> Response:
    return _page(request)


def _echo(form: Form) -> dict[str, str]:
    return {name: first(form, name) for name in FIELDS}


def _inputs(form: Form, assets: list[str]) -> tuple[PasswordsInputs | None, dict, list[str]]:
    fields: dict[str, list[str]] = {}
    general: list[str] = []
    asset_id = first(form, "asset_id")
    if asset_id not in assets:
        fields["asset_id"] = [
            "Elige un activo de tipo aplicación del inventario; si no hay, añádelo antes."
        ]
    days = first(form, "inactive_days")
    if not (days.isascii() and days.isdigit()):
        fields["inactive_days"] = [FIELD_TEXT["inactive_days"]]
    if fields.get("inactive_days") or fields.get("asset_id"):
        # el host se valida igualmente para avisar de todo a la vez
        _check_host(form, fields)
        return None, fields, general
    try:
        return (
            PasswordsInputs(asset_id=asset_id, inactive_days=int(days), host=first(form, "host")),
            fields,
            general,
        )
    except ValidationError as exc:
        for item in exc.errors(include_input=False, include_url=False):
            name = str(item["loc"][-1]) if item["loc"] else None
            if name in FIELD_TEXT:
                fields.setdefault(name, []).append(FIELD_TEXT[name])
            else:
                general.append(GENERIC)
        return None, fields, general


def _check_host(form: Form, fields: dict[str, list[str]]) -> None:
    try:
        PasswordsInputs(asset_id="x", inactive_days=1, host=first(form, "host"))
    except ValidationError:
        fields["host"] = [FIELD_TEXT["host"]]


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
            "Content-Disposition": 'attachment; filename="contrasenas.tar.gz"',
            "Cache-Control": "no-store",
        },
    )


PASSWORDS_ROUTES: list[BaseRoute] = [
    Route("/herramientas/contrasenas", asistente, methods=["GET"]),
    Route("/herramientas/contrasenas", generar, methods=["POST"]),
]
