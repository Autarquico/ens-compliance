"""Pasos de alcance: sistema, activos, servicios, dependencias y su aprobación."""

from datetime import datetime
from typing import get_args

from ens_evidence.models.enums import Role
from ens_evidence.scope import (
    Asset,
    AssetType,
    ScopeApprovalRejected,
    ScopeState,
    approve_scope,
    load_assets,
    scope_status,
)
from ens_evidence.scope.edit import (
    ScopeEditRejected,
    add_asset,
    add_dependency,
    add_service,
    include_asset,
    include_service,
    parse_field,
    set_system,
)
from ens_evidence.scope.inventory import (
    ASSETS_KEY,
    DEPENDENCIES_KEY,
    SERVICES_KEY,
    SYSTEM_KEY,
)
from ens_evidence.scope.models import REQUIRED_FIELDS
from ens_evidence.scope.skeleton import create_scope_skeleton
from ens_evidence.ui.forms import first
from ens_evidence.ui.store import UiStore
from ens_evidence.ui.views import format_instant
from ens_evidence.ui.wizard.base import (
    Context,
    Step,
    StepInput,
    StepRejected,
    declared_name,
    items,
    read_yaml,
    reject_removals,
    rejected_to_step,
    required,
)

ASSET_TYPE_LABELS = {
    AssetType.LAPTOP: "Portátil",
    AssetType.SERVER: "Servidor",
    AssetType.APPLICATION: "Aplicación",
    AssetType.DATABASE: "Base de datos",
    AssetType.CODE_HOSTING: "Alojamiento de código",
    AssetType.EDGE_NETWORK: "Red perimetral (CDN, cortafuegos)",
    AssetType.IDENTITY: "Identidad y accesos",
    AssetType.PASSWORD_MANAGER: "Gestor de contraseñas",
    AssetType.STORAGE: "Almacenamiento",
    AssetType.BACKUP: "Copias de seguridad",
    AssetType.PROVIDER: "Proveedor",
}
FIELD_LABELS = {
    "os": "Sistema operativo", "owner": "Responsable", "encrypted": "Disco cifrado",
    "usage": "Uso", "hostname": "Nombre de la máquina", "function": "Función",
    "environment": "Entorno", "location": "Ubicación", "service": "Servicio",
    "domain": "Dominio", "repo": "Repositorio", "database": "Base de datos",
    "engine": "Motor", "data": "Datos que guarda", "backup": "Copia de seguridad",
    "account": "Cuenta", "repos": "Repositorios (separados por comas)",
    "actions": "Automatizaciones", "zones": "Zonas (separadas por comas)", "tunnel": "Túnel",
    "waf": "Cortafuegos web", "access": "Acceso", "users": "Usuarios", "admins": "Administradores",
    "mfa": "Segundo factor", "mode": "Modalidad", "recovery": "Recuperación",
    "buckets": "Contenedores (separados por comas)", "encryption": "Cifrado",
    "source": "Origen", "destination": "Destino", "frequency": "Frecuencia",
    "restoration": "Prueba de restauración", "dependency": "Dependencia",
}  # fmt: skip
SUMMARY_FIELDS = ("hostname", "owner", "domain", "account", "engine", "service", "source")


def _included(store: UiStore, name: str) -> list[str]:
    return [str(i) for i in read_yaml(store, SYSTEM_KEY).get(name) or []]


def _is_bool(name: str) -> bool:
    annotation = Asset.model_fields[name].annotation
    return annotation is bool or bool in get_args(annotation)


def _asset_choices(store: UiStore) -> list[dict]:
    return [
        {"id": a["id"], "label": None}
        for a in items(read_yaml(store, ASSETS_KEY), "assets")
        if "id" in a
    ]


# --- sistema -----------------------------------------------------------------------------------


def _sistema_context(step: StepInput) -> Context:
    system = read_yaml(step.store, SYSTEM_KEY)
    return {
        "values": {
            "name": str(system.get("name") or ""),
            "description": str(system.get("description") or ""),
        }
    }


def _sistema_save(step: StepInput) -> None:
    values = required(step.form, {"name": "El nombre del sistema", "description": "La descripción"})
    if not step.store.exists(SYSTEM_KEY):
        create_scope_skeleton(step.store)
    with rejected_to_step(ScopeEditRejected):
        set_system(step.store, values["name"], values["description"], step.now)


def _sistema_done(store: UiStore, now: datetime) -> bool:
    return bool(read_yaml(store, SYSTEM_KEY).get("name"))


# --- activos -----------------------------------------------------------------------------------


def _asset_summary(asset: dict) -> str | None:
    return next((str(asset[f]) for f in SUMMARY_FIELDS if asset.get(f)), None)


def _new_asset(selected: str) -> dict | None:
    try:
        kind = AssetType(selected)
    except ValueError:
        return None
    return {
        "type": kind.value,
        "type_label": ASSET_TYPE_LABELS[kind],
        "fields": [
            {
                "name": name,
                "label": FIELD_LABELS.get(name, name),
                "kind": "bool" if _is_bool(name) else "text",
            }
            for name in REQUIRED_FIELDS[kind]
        ],
    }


def _activos_context(step: StepInput) -> Context:
    included = set(_included(step.store, "included_assets"))
    assets, _ = load_assets(step.store)
    listed = assets.assets if assets else []
    selected = first(step.form, "asset_type") or step.query.get("tipo", "")
    return {
        "assets": [
            {
                "id": a.id,
                "type": a.type.value,
                "type_label": ASSET_TYPE_LABELS[a.type],
                "summary": _asset_summary(a.model_dump(exclude_none=True)),
                "deployment": a.deployment.value,
                "included": a.id in included,
            }
            for a in listed
        ],
        "asset_types": [{"value": t.value, "label": ASSET_TYPE_LABELS[t]} for t in AssetType],
        "new_asset": _new_asset(selected),
        "values": {"included_assets": sorted(included)},
    }


def _add_asset(step: StepInput) -> None:
    form = step.form
    try:
        kind = AssetType(first(form, "asset_type"))
    except ValueError:
        raise StepRejected(form_errors=["Elige un tipo de activo."]) from None
    asset_id = required(form, {"asset_id": "El identificador"})["asset_id"]
    values: dict = {"id": asset_id, "type": kind.value, "deployment": "deployed"}
    errors: dict[str, list[str]] = {}
    if any(c.isspace() for c in asset_id):
        errors["asset_id"] = ["El identificador no puede llevar espacios."]
    for name in REQUIRED_FIELDS[kind]:
        if not (text := first(form, f"asset_{name}")):
            continue
        try:
            values[name] = parse_field(name, text)
        except ScopeEditRejected:
            label = FIELD_LABELS.get(name, name)
            errors[f"asset_{name}"] = [f"{label}: escribe sí o no."]
    if errors:
        raise StepRejected(errors)
    with rejected_to_step(ScopeEditRejected):
        add_asset(step.store, values, True, step.now)


def _activos_save(step: StepInput) -> None:
    if first(step.form, "accion") == "anadir":
        _add_asset(step)
        return
    checked = set(step.form.get("included_assets", []))
    reject_removals(
        checked, _included(step.store, "included_assets"), "included_assets", "un activo"
    )
    for asset_id in sorted(checked - set(_included(step.store, "included_assets"))):
        with rejected_to_step(ScopeEditRejected):
            include_asset(step.store, asset_id, step.now)


def _activos_done(store: UiStore, now: datetime) -> bool:
    return bool(_included(store, "included_assets"))


# --- servicios ---------------------------------------------------------------------------------


def _servicios_context(step: StepInput) -> Context:
    included = set(_included(step.store, "included_services"))
    return {
        "services": [
            {
                "id": s["id"],
                "name": str(s.get("name") or ""),
                "assets": [str(a) for a in s.get("assets") or []],
                "included": s["id"] in included,
            }
            for s in items(read_yaml(step.store, SERVICES_KEY), "services")
            if "id" in s
        ],
        "asset_choices": _asset_choices(step.store),
        "values": {"included_services": sorted(included)},
    }


def _servicios_save(step: StepInput) -> None:
    form = step.form
    checked = set(form.get("included_services", []))
    reject_removals(
        checked, _included(step.store, "included_services"), "included_services", "un servicio"
    )
    new_id, new_name = first(form, "service_id"), first(form, "service_name")
    new_assets = form.get("service_assets", [])
    adding = bool(new_id or new_name or new_assets)
    if adding:
        errors = {}
        if not new_id:
            errors["service_id"] = ["El identificador es obligatorio."]
        if not new_name:
            errors["service_name"] = ["El nombre es obligatorio."]
        if not new_assets:
            errors["service_assets"] = ["Elige al menos un activo del que dependa."]
        if errors:
            raise StepRejected(errors)
    for service_id in sorted(checked - set(_included(step.store, "included_services"))):
        with rejected_to_step(ScopeEditRejected):
            include_service(step.store, service_id, step.now)
    if adding:
        with rejected_to_step(ScopeEditRejected):
            add_service(step.store, new_id, new_name, new_assets, True, step.now)


def _servicios_done(store: UiStore, now: datetime) -> bool:
    return bool(_included(store, "included_services"))


# --- dependencias ------------------------------------------------------------------------------


def _dependencias_context(step: StepInput) -> Context:
    return {
        "dependencies": [
            {
                "id": d["id"],
                "asset": str(d.get("asset") or ""),
                "depends_on": str(d.get("depends_on") or ""),
                "purpose": d.get("purpose"),
            }
            for d in items(read_yaml(step.store, DEPENDENCIES_KEY), "dependencies")
            if "id" in d
        ],
        "asset_choices": _asset_choices(step.store),
    }


def _dependencias_save(step: StepInput) -> None:
    form = step.form
    if not any(first(form, name) for name in ("dependency_id", "asset", "depends_on", "purpose")):
        return
    values = required(
        form,
        {
            "dependency_id": "El identificador",
            "asset": "El activo",
            "depends_on": "El activo del que depende",
        },
    )
    with rejected_to_step(ScopeEditRejected):
        add_dependency(
            step.store,
            values["dependency_id"],
            values["asset"],
            values["depends_on"],
            first(form, "purpose") or None,
            step.now,
        )


def _dependencias_done(store: UiStore, now: datetime) -> bool:
    """Opcional para aprobar el alcance: con el alcance aprobado, el paso no queda pendiente."""
    return bool(items(read_yaml(store, DEPENDENCIES_KEY), "dependencies")) or _aprobacion_done(
        store, now
    )


# --- aprobación del alcance --------------------------------------------------------------------


def _aprobacion_context(step: StepInput) -> Context:
    status = scope_status(step.store)
    system = read_yaml(step.store, SYSTEM_KEY)
    approval = status.approval
    return {
        "scope_state": status.state.value,
        "scope_errors": status.errors if status.state is ScopeState.INVALID else [],
        "scope_summary": {
            "name": str(system.get("name") or ""),
            "assets_count": len(system.get("included_assets") or []),
            "services_count": len(system.get("included_services") or []),
        },
        "approval": {
            "approved_by": approval.approved_by,
            "approved_at": format_instant(approval.approved_at),
        }
        if approval and status.state is ScopeState.APPROVED
        else None,
    }


def _aprobacion_save(step: StepInput) -> None:
    if first(step.form, "accion") != "aprobar":
        return
    by = declared_name(first(step.form, "approved_by"), "Quién lo aprueba", "approved_by")
    try:
        approve_scope(step.store, by, Role.SYSTEM_OWNER, step.now)
    except ScopeApprovalRejected as exc:
        raise StepRejected(form_errors=exc.errors) from None


def _aprobacion_done(store: UiStore, now: datetime) -> bool:
    return scope_status(store).state is ScopeState.APPROVED


SCOPE_STEPS = (
    Step("sistema", "Sistema", "Cuéntanos qué sistema es", "alcance", "wizard/sistema.html",
         _sistema_done, _sistema_context, _sistema_save),
    Step("activos", "Activos", "Qué activos entran", "alcance", "wizard/activos.html",
         _activos_done, _activos_context, _activos_save),
    Step("servicios", "Servicios", "Qué servicios ofreces", "alcance", "wizard/servicios.html",
         _servicios_done, _servicios_context, _servicios_save),
    Step("dependencias", "Dependencias (opcional)", "De qué depende cada activo", "alcance",
         "wizard/dependencias.html", _dependencias_done, _dependencias_context, _dependencias_save,
         optional=True),
    Step("aprobacion-alcance", "Aprobar el alcance", "Aprobar el alcance", "alcance",
         "wizard/aprobacion_alcance.html", _aprobacion_done, _aprobacion_context, _aprobacion_save),
)  # fmt: skip
