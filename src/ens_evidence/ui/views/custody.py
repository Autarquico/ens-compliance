"""Registro de custodios del almacén de claves: quiénes guardan las partes y desde cuándo.

Es una revisión humana declarada: la herramienta no guarda las partes de desbloqueo, ni su
número ni su huella. Los campos «quién» llegan vacíos; nunca se prellenan con el correo de Access.
"""

import json
from datetime import UTC, datetime
from typing import Any

import yaml
from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import BaseRoute, Route

from ens_evidence.connectors.keystore.connector import SOURCE as KEYSTORE_SOURCE
from ens_evidence.evidence import list_manifests
from ens_evidence.kit.custody import (
    CustodianRegister,
    CustodianRegisterError,
    latest_register,
    record_custodians,
)
from ens_evidence.scope.inventory import load_assets
from ens_evidence.scope.models import AssetType
from ens_evidence.ui.audit import audit_write
from ens_evidence.ui.forms import Form, FormInvalid, FormTooLarge, first, read_form
from ens_evidence.ui.pages import error_page, identity_of, page_context, render
from ens_evidence.ui.store import UiStore
from ens_evidence.ui.views import format_instant
from ens_evidence.ui.views.matrix import REVIEW_ROLES, ROLE_LABELS
from ens_evidence.workspace import WorkspaceError

STEP = "claves-custodios"
MAX_CUSTODIANS = 10
ROWS = range(MAX_CUSTODIANS)
DATE_HINT = "Usa el formato AAAA-MM-DD."
THRESHOLD_MESSAGE = "M no puede ser mayor que N."
NUMBER_HINT = "Escribe un número entero, sin signo ni decimales."


def _application_assets(store: UiStore) -> list[str]:
    assets, _ = load_assets(store)
    if assets is None:
        return []
    return sorted(a.id for a in assets.assets if a.type is AssetType.APPLICATION)


def _only(assets: list[str]) -> str:
    return assets[0] if len(assets) == 1 else ""


def _latest_seal(store: UiStore) -> tuple[str | None, dict[str, Any] | None]:
    """El activo y el `seal-status` (n y t) de la última ejecución del conector, si se leen."""
    manifests = [m for m in list_manifests(store).manifests if m.source == KEYSTORE_SOURCE]
    if not manifests:
        return None, None
    last = max(manifests, key=lambda m: m.collected_at)
    try:
        data = json.loads(store.read_bytes(last.artifact.key))
        summary = data["data"]["sections"]["seal"]["summary"]
    except (WorkspaceError, ValueError, KeyError, TypeError):
        return last.asset_id, None
    return last.asset_id, summary if isinstance(summary, dict) else None


def _mismatch(register: CustodianRegister | None, seal: dict[str, Any] | None) -> list[str]:
    if register is None or seal is None:
        return []
    n, t = seal.get("n"), seal.get("t")
    warnings = []
    if isinstance(n, int) and n != register.key_shares:
        warnings.append(
            f"N no casa: el registro declara {register.key_shares} partes y OpenBao tiene {n}."
        )
    if isinstance(t, int) and t != register.key_threshold:
        warnings.append(
            f"M no casa: el registro declara {register.key_threshold} para desbloquear y "
            f"OpenBao tiene {t}."
        )
    return warnings


def _current(register: CustodianRegister | None) -> dict[str, Any] | None:
    if register is None:
        return None
    return {
        "custodians": [
            {"name": c.name, "since": format_instant(c.since)} for c in register.custodians
        ],
        "key_shares": register.key_shares,
        "key_threshold": register.key_threshold,
        "reviewer": register.review.reviewer,
        "role": ROLE_LABELS[register.review.role],
        "reviewed_at": format_instant(register.review.reviewed_at),
        "valid_until": format_instant(register.review.valid_until),
    }


def _page(
    request: Request,
    *,
    values: dict[str, str] | None = None,
    errors: dict[str, list[str]] | None = None,
    form_errors: list[str] | None = None,
    status_code: int = 200,
) -> Response:
    store: UiStore = request.app.state.store
    now = request.app.state.clock()
    seal_asset, seal = _latest_seal(store)
    assets = _application_assets(store)
    default_asset = seal_asset if seal_asset in assets else _only(assets)
    register, unreadable = latest_register(store, now, default_asset)
    return render(
        request,
        "views/herramientas_claves_custodios.html",
        status_code=status_code,
        **page_context(request, "herramientas", "Custodios del almacén de claves"),
        values={"asset_id": default_asset} | (values or {}),
        errors=errors or {},
        form_errors=form_errors or [],
        application_assets=assets,
        rows=list(ROWS),
        review_roles=[{"value": r.value, "label": ROLE_LABELS[r]} for r in REVIEW_ROLES],
        current=_current(register),
        unreadable_count=len(unreadable),
        mismatches=_mismatch(register, seal),
        saved=request.query_params.get("guardado") == "1",
    )


def formulario(request: Request) -> Response:
    return _page(request)


def _echo(form: Form, errors: dict[str, list[str]] | None = None) -> dict[str, str]:
    """Lo escrito, salvo los nombres rechazados: un correo, DNI o secreto no se devuelve."""
    names = ["asset_id", "key_shares", "key_threshold", "reviewer", "role"]
    names += ["reviewed_on", "valid_until_on"]
    names += [f"{kind}_{i}" for i in ROWS for kind in ("custodian_name", "custodian_since")]
    rejected = errors or {}
    declared_names = {"reviewer", *(f"custodian_name_{i}" for i in ROWS)}
    return {
        name: "" if name in declared_names and name in rejected else first(form, name)
        for name in names
    }


def _day(text: str) -> datetime | None:
    try:
        return datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError:
        return None


def _declaration(
    form: Form, assets: list[str], now: datetime
) -> tuple[dict[str, Any] | None, dict[str, list[str]]]:
    errors: dict[str, list[str]] = {}

    def fail(field: str, message: str) -> None:
        errors.setdefault(field, []).append(message)

    def number(field: str) -> int:
        text = first(form, field)
        if text.isascii() and text.isdigit():
            return int(text)
        fail(field, NUMBER_HINT)
        return 0

    def date(field: str) -> datetime | None:
        value = _day(first(form, field))
        if value is None:
            fail(field, DATE_HINT)
        return value

    if first(form, "asset_id") not in assets:
        fail("asset_id", "Elige un activo de tipo aplicación del inventario.")
    custodians = []
    for i in ROWS:
        name, since = first(form, f"custodian_name_{i}"), first(form, f"custodian_since_{i}")
        if not name and not since:
            continue
        if not name:
            fail(f"custodian_name_{i}", "Indica el nombre completo.")
        since_at = date(f"custodian_since_{i}")
        custodians.append({"name": name, "since": since_at})
    if not custodians:
        fail("custodian_name_0", "Indica al menos un custodio.")
    reviewed_at = date("reviewed_on")
    if reviewed_at is not None and reviewed_at > now:
        fail("reviewed_on", "La fecha de la revisión no puede ser futura.")
    declaration = {
        "custodians": custodians,
        "key_shares": number("key_shares"),
        "key_threshold": number("key_threshold"),
        "review": {
            "reviewer": first(form, "reviewer"),
            "role": first(form, "role"),
            "reviewed_at": reviewed_at,
            "valid_until": date("valid_until_on"),
        },
    }
    return (None if errors else declaration), errors


def _validation_errors(exc: ValidationError) -> tuple[dict[str, list[str]], list[str]]:
    """Errores por campo y generales; los mensajes de los validadores no llevan el valor."""
    fields: dict[str, list[str]] = {}
    general: list[str] = []
    for item in exc.errors(include_input=False, include_url=False, include_context=False):
        loc = item["loc"]
        message = str(item["msg"]).removeprefix("Value error, ")
        if loc[:1] == ("custodians",) and len(loc) >= 3:
            field = f"custodian_{'name' if loc[2] == 'name' else 'since'}_{loc[1]}"
        elif loc[:2] == ("review", "reviewer"):
            field = "reviewer"
        elif loc[:2] == ("review", "role"):
            field, message = "role", "Elige uno de los roles."
        elif loc == () and item["type"] == "value_error":
            field, message = "key_threshold", THRESHOLD_MESSAGE
        elif loc[:2] == ("review", "valid_until") or loc == ("review",):
            field, message = "valid_until_on", "Debe ser posterior a la fecha de la revisión."
        elif loc[:1] in (("key_shares",), ("key_threshold",)):
            field = str(loc[0])
            message = "Debe ser un número mayor que 0." if "mayor" not in message else message
        else:
            general.append("Revisa los datos del formulario.")
            continue
        fields.setdefault(field, []).append(message)
    return fields, general


async def guardar(request: Request) -> Response:
    try:
        form = await read_form(request)
    except FormTooLarge:
        return error_page(request, 413, "El formulario es demasiado grande.")
    except FormInvalid:
        return error_page(request, 400, "No se ha entendido el formulario.")
    store: UiStore = request.app.state.store.recording()
    now, email = request.app.state.clock(), identity_of(request).email
    declaration, errors = _declaration(form, _application_assets(store), now)
    general: list[str] = []
    if declaration is not None:
        try:
            record_custodians(
                store,
                yaml.safe_dump(_jsonable(declaration), allow_unicode=True).encode(),
                now,
                asset_id=first(form, "asset_id"),
            )
        except ValidationError as exc:
            errors, general = _validation_errors(exc)
        except CustodianRegisterError as exc:
            return _page(
                request,
                values=_echo(form),
                form_errors=[str(exc)],
                status_code=409 if "identificador" in str(exc) else 422,
            )
    if errors or general:
        return _page(
            request,
            values=_echo(form, errors),
            errors=errors,
            form_errors=general or ["No se ha guardado. Revisa los campos marcados."],
            status_code=422,
        )
    audit_write(identity=email, step=STEP, action="registrar", keys=store.written, at=now)
    return RedirectResponse("/herramientas/claves/custodios?guardado=1", status_code=303)


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


CUSTODY_ROUTES: list[BaseRoute] = [
    Route("/herramientas/claves/custodios", formulario, methods=["GET"]),
    Route("/herramientas/claves/custodios", guardar, methods=["POST"]),
]
