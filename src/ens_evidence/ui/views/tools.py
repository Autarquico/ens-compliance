"""Herramientas: panel del kit de copias y asistente que genera su paquete de instalación.

La UI no llama a restic ni a Backrest y no escribe en el workspace: el estado sale de la última
ejecución de checks del conector `backups`, y el paquete (con las claves) se devuelve una sola vez
como descarga. Nada de lo que contiene se guarda ni se audita; solo el hecho.
"""

from collections.abc import Mapping
from typing import Any

from pydantic import SecretStr, ValidationError
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import BaseRoute, Route

from ens_evidence.checks.store import scan_runs
from ens_evidence.connectors.keystore.checks import SEALED_CODE, UNSEALED
from ens_evidence.kit.backups import (
    ALLOWED_VOLUMES,
    DEFAULT_MIN_CHECK_READ_DATA_PERCENT,
    BackupInputError,
    BackupInputs,
    BundleSecrets,
    generate_repo_password,
    render_bundle,
    render_host_bundle,
)
from ens_evidence.kit.registry import KIT_TOOLS, KitTool, tool_for
from ens_evidence.models.enums import CheckStatus
from ens_evidence.scope.inventory import load_assets
from ens_evidence.scope.models import AssetType
from ens_evidence.ui.audit import audit_write
from ens_evidence.ui.forms import Form, FormInvalid, FormTooLarge, first, read_form
from ens_evidence.ui.pages import error_page, identity_of, page_context, render
from ens_evidence.ui.store import UiStore
from ens_evidence.ui.views import NO_DATE, format_instant

CONNECTOR = "backups"
KEYSTORE_CONNECTOR = "keystore"
STEP = "copias"
ENABLE_COMMAND = "deploy/box.sh kit enable copias <fichero>"

STATES = {
    "ok": ("ok", "Al día"),
    "sealed": ("bad", "Sellado"),
    "problems": ("bad", "Con problemas"),
    "down": ("bad", "Caída"),
    "nodata": ("neutral", "Sin datos"),
}
VOLUME_LABELS = {"data": "Datos de la caja (el workspace con tus evidencias)"}
VARIANTS = {"caja": "En la caja (este servidor)", "servidor": "En otro servidor Linux"}
SECRET_FIELDS = (
    "write_access_key_id",
    "write_secret_access_key",
    "read_access_key_id",
    "read_secret_access_key",
)
INT_FIELDS = (
    "lock_days",
    "retention_days",
    "max_backup_age_hours",
    "max_check_age_days",
    "restore_test_validity_days",
    "min_check_read_data_percent",
)
ECHOED = (
    "id",
    "asset_id",
    "instance",
    "endpoint",
    "bucket",
    "prefix",
    "cron",
    "variant",
    "host_paths",
    *INT_FIELDS,
)
FIELDS = frozenset({*ECHOED, "volumes", *SECRET_FIELDS})
GENERIC = "Revisa este campo."


class Rejected(Exception):
    """Lo que no se pudo hacer, por campo y en general; nunca con valores del formulario."""

    def __init__(
        self,
        fields: dict[str, list[str]],
        general: list[str],
    ) -> None:
        super().__init__("rechazado")
        self.fields = fields
        self.general = general


# --- estado del kit (solo evidencia del workspace) -----------------------------------------------


def kit_state(store: UiStore, connector: str) -> dict[str, Any]:
    """`ok`, `problems`, `down` o `nodata` (y `sealed` en claves), con la fecha de la última
    comprobación guardada del conector."""
    scan = scan_runs(store, connector)
    last = scan.last(connector)
    if last is None:
        key = "problems" if connector in scan.unreadable else "nodata"
        return _state(key, None)
    run = last.run
    if connector == KEYSTORE_CONNECTOR and _is_sealed(run.results):
        return _state("sealed", run.finished_at)
    unanswered = {CheckStatus.ERROR, CheckStatus.UNKNOWN}
    if not run.complete or all(r.status in unanswered for r in run.results):
        return _state("down", run.finished_at)
    healthy = all(r.status is CheckStatus.PASS for r in run.results)
    return _state("ok" if healthy else "problems", run.finished_at)


def _is_sealed(results: list[Any]) -> bool:
    return any(
        r.check_id == UNSEALED and r.status is CheckStatus.FAIL and r.reason_code == SEALED_CODE
        for r in results
    )


def _state(key: str, at: Any) -> dict[str, Any]:
    tone, label = STATES[key]
    return {
        "key": key,
        "tone": tone,
        "label": label,
        "checked_at": format_instant(at) if at else NO_DATE,
    }


def kit_offer(
    store: UiStore,
    measure_id: str,
    applicable: bool,
    clause_texts: Mapping[str, str] | None = None,
) -> dict[str, Any] | None:
    """Propuesta del kit para una medida aplicable que apoya, mientras su conector no tenga
    evidencia. `clause_texts` (id de cláusula -> texto oficial) viene del catálogo que ya
    carga la ficha; una cláusula sin texto se enseña solo con su id."""
    texts = clause_texts or {}

    def clauses(ids: tuple[str, ...]) -> list[dict[str, str | None]]:
        return [{"id": i, "text": texts.get(i)} for i in ids]

    tool = tool_for(measure_id)
    if tool is None or not applicable or kit_state(store, tool.connector)["key"] != "nodata":
        return None
    return {
        "name": tool.id,
        "tool_name": tool.name,
        "cta": tool.cta,
        "url": tool.wizard_url,
        "covers": tool.covers,
        "not_covers": tool.not_covers,
        "covers_clauses": clauses(tool.covers_clauses),
        "partial_clauses": clauses(tool.partial_clauses),
        "not_covered_clauses": clauses(tool.not_covered_clauses),
        "ram": tool.ram,
    }


# --- panel ---------------------------------------------------------------------------------------


def panel(request: Request) -> Response:
    store = request.app.state.store
    hosts = request.app.state.config.kit_hosts
    return render(
        request,
        "views/herramientas.html",
        **page_context(request, "herramientas", "Herramientas"),
        tools=[_tool_card(store, tool, hosts.get(tool.id)) for tool in KIT_TOOLS],
    )


def _tool_card(store: UiStore, tool: KitTool, host: str | None) -> dict[str, Any]:
    return {
        "id": tool.id,
        "name": tool.name,
        "purpose": tool.purpose,
        "state": kit_state(store, tool.connector),
        "measures": list(tool.measures),
        "measures_note": tool.measures_note,
        "hints": tool.hints,
        "open_url": _open_url(host),
        "wizard_url": tool.wizard_url,
        "cta": tool.cta,
        "help_url": f"/ayuda/{tool.help_slug}",
        "help_label": tool.help_label,
        "ram": tool.ram,
    }


def _open_url(host: str | None) -> str | None:
    return f"https://{host}" if host else None


# --- asistente -----------------------------------------------------------------------------------


def _backup_assets(store: UiStore) -> list[str]:
    assets, _ = load_assets(store)
    if assets is None:
        return []
    return sorted(a.id for a in assets.assets if a.type is AssetType.BACKUP)


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
        "views/herramientas_copias.html",
        status_code=status_code,
        **page_context(request, "herramientas", "Copias"),
        values={
            "variant": "caja",
            "min_check_read_data_percent": str(DEFAULT_MIN_CHECK_READ_DATA_PERCENT),
        }
        | (values or {}),
        errors=errors or {},
        form_errors=form_errors or [],
        backup_assets=_backup_assets(request.app.state.store),
        volumes=[{"value": v, "label": VOLUME_LABELS.get(v, v)} for v in sorted(ALLOWED_VOLUMES)],
        variants=[{"value": k, "label": v} for k, v in VARIANTS.items()],
        default_read_percent=DEFAULT_MIN_CHECK_READ_DATA_PERCENT,
        enable_command=ENABLE_COMMAND,
    )


def asistente(request: Request) -> Response:
    return _page(request)


def _echo(form: Form) -> dict[str, Any]:
    """Lo escrito que se puede devolver al formulario: nunca las claves."""
    echoed: dict[str, Any] = {name: first(form, name) for name in ECHOED}
    echoed["volumes"] = list(form.get("volumes", []))
    return echoed


def _paths(form: Form) -> tuple[str, ...]:
    return tuple(line.strip() for line in first(form, "host_paths").splitlines() if line.strip())


def _integers(form: Form, fields: dict[str, list[str]]) -> dict[str, int]:
    numbers: dict[str, int] = {}
    for name in INT_FIELDS:
        text = first(form, name)
        if not text.isascii() or not text.isdigit():
            fields.setdefault(name, []).append("Escribe un número entero, sin signo ni decimales.")
        else:
            numbers[name] = int(text)
    return numbers


def _field_of(loc: tuple[Any, ...]) -> str | None:
    return str(loc[-1]) if loc else None


def _inputs(
    form: Form, fields: dict[str, list[str]], general: list[str], assets: list[str]
) -> BackupInputs | None:
    numbers = _integers(form, fields)
    variant = first(form, "variant")
    if variant not in VARIANTS:
        fields.setdefault("variant", []).append("Elige dónde se instala.")
    if first(form, "asset_id") not in assets:
        fields.setdefault("asset_id", []).append(
            "Elige un activo de tipo copia del inventario; si no hay, añádelo antes."
        )
    if fields:
        return None
    data = {
        "id": first(form, "id"),
        "asset_id": first(form, "asset_id"),
        "destination": {
            "endpoint": first(form, "endpoint"),
            "bucket": first(form, "bucket"),
            "prefix": first(form, "prefix"),
            "lock_days": numbers["lock_days"],
        },
        "schedule": {
            "cron": first(form, "cron"),
            **{k: v for k, v in numbers.items() if k != "lock_days"},
        },
        "volumes": tuple(form.get("volumes", [])),
        "host_paths": _paths(form),
        "instance": first(form, "instance") or "caja",
    }
    if variant == "servidor" and not first(form, "instance"):
        fields.setdefault("instance", []).append(
            "Pon un nombre al servidor (minúsculas, cifras y _)."
        )
    try:
        return BackupInputs.model_validate(data)
    except ValidationError as exc:
        _collect(exc, fields, general)
        return None


def _text(item: Any) -> str:
    kind = item["type"]
    if kind == "value_error":
        return item["msg"].removeprefix("Value error, ")
    if kind == "greater_than":
        return "Debe ser mayor que 0."
    if kind in ("less_than_equal", "greater_than_equal"):
        return "Debe estar entre 0 y 100."
    return GENERIC


def _collect(exc: ValidationError, fields: dict[str, list[str]], general: list[str]) -> None:
    for item in exc.errors(include_input=False, include_url=False):
        text = _text(item)
        name = _field_of(item["loc"])
        if name in FIELDS:
            fields.setdefault(name, []).append(text)
        else:
            general.append(text)


def _secrets(form: Form, fields: dict[str, list[str]], general: list[str]) -> BundleSecrets | None:
    values = {name: SecretStr(first(form, name)) for name in SECRET_FIELDS}
    try:
        return BundleSecrets(repo_password=generate_repo_password(), **values)
    except ValidationError as exc:
        for item in exc.errors(include_input=False, include_url=False):
            text = item["msg"].removeprefix("Value error, ")
            name, _, rest = text.partition(": ")
            if name in SECRET_FIELDS and rest:
                fields.setdefault(name, []).append(rest)
            else:
                general.append(text if item["type"] == "value_error" else GENERIC)
        return None


def _bundle(form: Form, assets: list[str], now: Any) -> tuple[bytes, str, str]:
    fields: dict[str, list[str]] = {}
    general: list[str] = []
    inputs = _inputs(form, fields, general, assets)
    secret = _secrets(form, fields, general)
    if inputs is None or secret is None or fields or general:
        raise Rejected(fields, general)
    try:
        if first(form, "variant") == "servidor":
            return render_host_bundle(inputs, secret, now), inputs.id, "paquete-servidor"
        return render_bundle(inputs, secret, now), inputs.id, "paquete-caja"
    except BackupInputError as exc:
        raise Rejected({}, [str(exc)]) from None


async def generar(request: Request) -> Response:
    try:
        form = await read_form(request)
    except FormTooLarge:
        return error_page(request, 413, "El formulario es demasiado grande.")
    except FormInvalid:
        return error_page(request, 400, "No se ha entendido el formulario.")
    now = request.app.state.clock()
    try:
        data, kit_id, action = _bundle(form, _backup_assets(request.app.state.store), now)
    except Rejected as rejected:
        return _page(
            request,
            values=_echo(form),
            errors=rejected.fields,
            form_errors=rejected.general
            or ["No se ha generado el paquete. Revisa los campos marcados."],
            status_code=422,
        )
    audit_write(identity=identity_of(request).email, step=STEP, action=action, keys=[], at=now)
    return Response(
        data,
        media_type="application/gzip",
        headers={
            "Content-Disposition": f'attachment; filename="copias-{kit_id}.tar.gz"',
            "Cache-Control": "no-store",
        },
    )


TOOLS_ROUTES: list[BaseRoute] = [
    Route("/herramientas", panel, methods=["GET"]),
    Route("/herramientas/copias", asistente, methods=["GET"]),
    Route("/herramientas/copias", generar, methods=["POST"]),
]
