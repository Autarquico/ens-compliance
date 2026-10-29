"""Secciones normalizadas de la recogida de Google Workspace.

Cada sección tiene su `CollectionStatus`. Solo se persisten campos de lista blanca con
tipos estrictos: un campo ausente o `null` es desconocido (`None`), nunca `false`. Los
datos de recuperación salen como booleanos, los eventos como recuentos y los parámetros de
eventos, IPs y correos de actores no salen del parseo. Los administradores se unen a los
usuarios por id inmutable, no por correo.
"""

import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    ValidationError,
)

from ens_evidence.connectors.google_workspace.auth import (
    CollectionAborted,
    GoogleWorkspaceError,
)
from ens_evidence.connectors.google_workspace.client import WorkspaceClient, segment
from ens_evidence.connectors.google_workspace.config import GoogleWorkspaceConfig
from ens_evidence.connectors.sections import Section, worst
from ens_evidence.models.enums import CollectionStatus

WINDOW_DAYS = 30
USERS_PAGE_SIZE = 500
ROLES_PAGE_SIZE = 100
REPORTS_PAGE_SIZE = 1000
MAX_ROLE_QUERIES = 5000
NAME_LIMIT = 100
USER_FIELDS = (
    "nextPageToken,users(id,primaryEmail,suspended,archived,isAdmin,isDelegatedAdmin,"
    "isEnrolledIn2Sv,isEnforcedIn2Sv,recoveryEmail,recoveryPhone)"
)
_ID = re.compile(r"[0-9A-Za-z_-]{1,64}")
_CLIENT_ID = re.compile(r"[0-9A-Za-z._-]{1,200}")
_EVENT_NAME = re.compile(r"[A-Za-z][0-9A-Za-z_]{0,79}")
_SCOPE = re.compile(r"[!-~]{1,200}")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_EMAIL = re.compile(r"[^@\s\x00-\x1f\x7f]{1,64}@[^@\s\x00-\x1f\x7f]{3,255}")
_SCOPE_TYPES = ("CUSTOMER", "ORG_UNIT")


def _email(value: str) -> str:
    if not _EMAIL.fullmatch(value):
        raise ValueError("correo no válido")
    return value


def _identifier(value: str) -> str:
    if not _ID.fullmatch(value):
        raise ValueError("identificador no válido")
    return value


Email = Annotated[str, AfterValidator(_email)]
Identifier = Annotated[str, AfterValidator(_identifier)]


class _Raw(BaseModel):
    """Forma mínima esperada de la API; lo que no se declara aquí se descarta."""

    model_config = ConfigDict(extra="ignore")


class _Customer(_Raw):
    id: Identifier
    domain: str = Field(alias="customerDomain")


class _User(_Raw):
    id: Identifier
    email: Email = Field(alias="primaryEmail")
    suspended: StrictBool | None = None
    archived: StrictBool | None = None
    is_admin: StrictBool | None = Field(None, alias="isAdmin")
    is_delegated_admin: StrictBool | None = Field(None, alias="isDelegatedAdmin")
    enrolled_2sv: StrictBool | None = Field(None, alias="isEnrolledIn2Sv")
    enforced_2sv: StrictBool | None = Field(None, alias="isEnforcedIn2Sv")
    recovery_email: str | None = Field(None, alias="recoveryEmail")
    recovery_phone: str | None = Field(None, alias="recoveryPhone")


class _Role(_Raw):
    id: Identifier = Field(alias="roleId")
    name: str | None = Field(None, alias="roleName")
    super_admin: StrictBool | None = Field(None, alias="isSuperAdminRole")


class _Assignment(_Raw):
    role_id: Identifier = Field(alias="roleId")
    assigned_to: Identifier = Field(alias="assignedTo")
    scope_type: str | None = Field(None, alias="scopeType")


class _Parameter(_Raw):
    name: str
    value: str | None = None
    multi_value: list[str] | None = Field(None, alias="multiValue")


class _Event(_Raw):
    type: str | None = None
    name: str | None = None
    parameters: list[_Parameter] = Field(default_factory=list)


class _Actor(_Raw):
    email: str | None = None
    profile_id: str | None = Field(None, alias="profileId")


class _ActivityId(_Raw):
    time: datetime


class _Activity(_Raw):
    id: _ActivityId
    actor: _Actor | None = None
    events: list[_Event] = Field(default_factory=list)


def _attempt[T](
    fetch: Callable[[], T], on_error: Callable[[GoogleWorkspaceError], None] | None = None
) -> tuple[CollectionStatus, str | None, T | None]:
    """Ejecuta una lectura; un fallo saneado se convierte en estado y motivo."""
    try:
        return CollectionStatus.SUCCESS, None, fetch()
    except CollectionAborted:
        raise
    except GoogleWorkspaceError as exc:
        if on_error:
            on_error(exc)
        return exc.status, exc.message, None
    except ValidationError:
        return CollectionStatus.ERROR, "respuesta con forma inesperada", None


def _failed(status: CollectionStatus, reason: str | None, **summary: Any) -> Section:
    return Section(status=status, reason=reason, summary=summary)


def _instant(moment: datetime | None) -> str | None:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if moment else None


def _present(value: str | None) -> bool | None:
    return None if value is None else bool(value)


def _choice(value: str | None, allowed: tuple[str, ...]) -> str | None:
    if value is None:
        return None
    return value if value in allowed else "other"


def _label(value: str | None) -> str | None:
    if value is None:
        return None
    return _CONTROL.sub("", value).strip()[:NAME_LIMIT] or None


def _customer_path(config: GoogleWorkspaceConfig, *rest: str) -> str:
    return "/admin/directory/v1/customer/" + "/".join([segment(config.customer), *rest])


def probe_customer(client: WorkspaceClient, config: GoogleWorkspaceConfig) -> dict[str, Any]:
    """`customers.get`: confirma que la credencial funciona y que el cliente es el esperado."""
    path = "/admin/directory/v1/customers/" + segment(config.customer)
    customer = _Customer.model_validate(client.get(path))
    if config.customer != "my_customer" and customer.id != config.customer:
        raise CollectionAborted("el cliente de Workspace no coincide con la configuración")
    if customer.domain.lower() not in config.domains:
        raise CollectionAborted("el dominio principal no está en domains de la configuración")
    return {"id": customer.id, "primary_domain": customer.domain.lower()}


def verify_customer(
    client: WorkspaceClient,
    config: GoogleWorkspaceConfig,
    expected: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Sonda de credencial y cliente; sin veredicto concluyente la recogida se aborta.

    Se llama al inicio y, con `expected`, al final: una revocación o un cambio de cliente a
    mitad invalida todo. Un rechazo de la credencial y un fallo transitorio o de permisos al
    sondear se distinguen en el motivo.
    """
    try:
        summary = probe_customer(client, config)
    except CollectionAborted:
        raise
    except GoogleWorkspaceError as exc:
        raise CollectionAborted(
            f"no se pudo verificar la credencial ni el cliente (no es un rechazo): {exc.message}"
        ) from None
    except ValidationError:
        raise CollectionAborted(
            "no se pudo verificar el cliente: respuesta con forma inesperada"
        ) from None
    if expected is not None and summary != expected:
        raise CollectionAborted("el cliente de Workspace cambió durante la recogida")
    return summary


def collect_users(client: WorkspaceClient, config: GoogleWorkspaceConfig) -> Section:
    def fetch() -> list[dict[str, Any]]:
        raws = client.pages(
            "/admin/directory/v1/users",
            "users",
            {
                "customer": config.customer,
                "projection": "basic",
                "maxResults": USERS_PAGE_SIZE,
                "fields": USER_FIELDS,
            },
        )
        users = [_read_user(raw) for raw in raws]
        ids = [user.id for user in users if user.id is not None]
        if len(set(ids)) != len(ids):
            raise GoogleWorkspaceError(
                CollectionStatus.ERROR, "usuarios duplicados en la respuesta"
            )
        return [_normalized(user) for user in users]

    status, reason, items = _attempt(fetch)
    if items is None:
        return _failed(status, reason)
    unreadable = sum(1 for item in items if item.get("unreadable"))
    return Section(
        status=CollectionStatus.SUCCESS,
        items=items,
        summary={"count": len(items), "unreadable": unreadable},
    )


@dataclass(frozen=True)
class _Unreadable:
    id: str | None


def _read_user(raw: Any) -> _User | _Unreadable:
    """Un usuario que no cumple la forma esperada no invalida al resto: queda ilegible."""
    try:
        return _User.model_validate(raw)
    except ValidationError:
        raw_id = raw.get("id") if isinstance(raw, dict) else None
        return _Unreadable(raw_id if isinstance(raw_id, str) and _ID.fullmatch(raw_id) else None)


def _normalized(user: _User | _Unreadable) -> dict[str, Any]:
    if isinstance(user, _Unreadable):
        return {"id": user.id, "unreadable": True}
    return {
        "id": user.id,
        "email": user.email,
        "suspended": user.suspended,
        "archived": user.archived,
        "is_admin": user.is_admin,
        "is_delegated_admin": user.is_delegated_admin,
        "enrolled_2sv": user.enrolled_2sv,
        "enforced_2sv": user.enforced_2sv,
        "has_recovery_email": _present(user.recovery_email),
        "has_recovery_phone": _present(user.recovery_phone),
    }


def _role_catalog(client: WorkspaceClient, config: GoogleWorkspaceConfig) -> dict[str, _Role]:
    roles = [
        _Role.model_validate(raw)
        for raw in client.pages(
            _customer_path(config, "roles"), "items", {"maxResults": ROLES_PAGE_SIZE}
        )
    ]
    return {role.id: role for role in roles}


def collect_admins(
    client: WorkspaceClient, config: GoogleWorkspaceConfig, users: Section
) -> Section:
    """Unión de los flags de `users` y las asignaciones de rol (directas y por grupo).

    Un usuario cuyas asignaciones no se pudieron consultar queda con `roles: None` y su
    estado: ausencia de datos no es ausencia de roles.
    """
    if users.status is not CollectionStatus.SUCCESS:
        reason = f"depende de users: {users.reason or users.status.value}"
        return _failed(users.status, reason, complete=False)
    catalog_status, catalog_reason, catalog = _attempt(lambda: _role_catalog(client, config))
    known = catalog or {}
    items: list[dict[str, Any]] = []
    blocked: tuple[CollectionStatus, str | None] | None = None
    for position, user in enumerate(users.items):
        if user.get("unreadable"):
            items.append(_unreadable_admin(user))
            continue
        item: dict[str, Any] = {
            "user_id": user["id"],
            "is_admin": user["is_admin"],
            "is_delegated_admin": user["is_delegated_admin"],
        }
        if position >= MAX_ROLE_QUERIES:
            blocked = (CollectionStatus.ERROR, "límite de consultas de roles alcanzado")
        if blocked is not None:
            items.append({**item, "status": blocked[0], "reason": blocked[1], "roles": None})
            continue
        failures: list[GoogleWorkspaceError] = []
        status, reason, roles = _attempt(
            lambda user=user: _user_roles(client, config, user, known), failures.append
        )
        if any(_stops_queries(failure) for failure in failures):
            blocked = (status, reason)
        items.append(
            {**item, "status": status, **({"reason": reason} if reason else {}), "roles": roles}
        )
    statuses = [item["status"] for item in items] + [catalog_status]
    overall = worst(statuses)
    return Section(
        status=overall,
        reason=None
        if overall is CollectionStatus.SUCCESS
        else catalog_reason or _first_reason(items),
        items=items,
        summary={
            "complete": overall is CollectionStatus.SUCCESS,
            "users": len(items),
            "roles_catalog": {"status": catalog_status},
        },
    )


def _unreadable_admin(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "user_id": user["id"],
        "is_admin": None,
        "is_delegated_admin": None,
        "status": CollectionStatus.ERROR,
        "reason": "usuario ilegible: no se consultan sus roles",
        "roles": None,
    }


def _stops_queries(failure: GoogleWorkspaceError) -> bool:
    """Sin permiso o sin cuota, las consultas del resto de usuarios fallarán igual."""
    return failure.quota or failure.status in (
        CollectionStatus.PERMISSION_DENIED,
        CollectionStatus.NOT_SUPPORTED,
    )


def _first_reason(items: list[dict[str, Any]]) -> str | None:
    return next((item["reason"] for item in items if item.get("reason")), None)


def _user_roles(
    client: WorkspaceClient,
    config: GoogleWorkspaceConfig,
    user: dict[str, Any],
    catalog: dict[str, _Role],
) -> list[dict[str, Any]]:
    assignments = [
        _Assignment.model_validate(raw)
        for raw in client.pages(
            _customer_path(config, "roleassignments"),
            "items",
            {
                "userKey": user["id"],
                "includeIndirectRoleAssignments": "true",
                "maxResults": ROLES_PAGE_SIZE,
            },
        )
    ]
    return [
        {
            "role_id": a.role_id,
            "role_name": _label(catalog[a.role_id].name) if a.role_id in catalog else None,
            "super_admin_role": catalog[a.role_id].super_admin if a.role_id in catalog else None,
            "direct": a.assigned_to == user["id"],
            "scope_type": _choice(a.scope_type, _SCOPE_TYPES),
        }
        for a in assignments
    ]


def _window(now: datetime) -> tuple[datetime, datetime]:
    return now - timedelta(days=WINDOW_DAYS), now


def _activities(
    client: WorkspaceClient, application: str, window: tuple[datetime, datetime]
) -> list[_Activity]:
    start, end = window
    return [
        _Activity.model_validate(raw)
        for raw in client.pages(
            f"/admin/reports/v1/activity/users/all/applications/{segment(application)}",
            "items",
            {
                "startTime": _instant(start),
                "endTime": _instant(end),
                "maxResults": REPORTS_PAGE_SIZE,
            },
        )
    ]


def _report_summary(
    window: tuple[datetime, datetime], activities: list[_Activity]
) -> dict[str, Any]:
    times = [activity.id.time for activity in activities]
    return {
        "complete": True,
        "requested_start": _instant(window[0]),
        "requested_end": _instant(window[1]),
        "observed_first": _instant(min(times, default=None)),
        "observed_last": _instant(max(times, default=None)),
        "activities": len(activities),
    }


def collect_oauth_token_events(client: WorkspaceClient, now: datetime) -> Section:
    """Eventos de token observados (no un inventario): vacío no prueba que no haya apps."""
    window = _window(now)
    status, reason, activities = _attempt(lambda: _activities(client, "token", window))
    if activities is None:
        return _failed(status, reason, complete=False)
    apps: dict[tuple[str, str | None], dict[str, Any]] = {}
    without_actor = 0
    unreadable_events = 0
    for activity in activities:
        actor = activity.actor
        actor_key = (actor.profile_id or actor.email) if actor else None
        without_actor += actor_key is None
        for event in activity.events:
            parameters = {p.name: p for p in event.parameters}
            raw_client = parameters.get("client_id")
            raw_name = parameters.get("app_name")
            client_id = raw_client.value if raw_client else None
            app = apps.setdefault(
                (
                    client_id if client_id and _CLIENT_ID.fullmatch(client_id) else "other",
                    _label(raw_name.value) if raw_name else None,
                ),
                {"events": Counter(), "scopes": set(), "actors": set()},
            )
            app["events"][_event_name(event.name)] += 1
            scopes = _event_scopes(parameters.get("scope"))
            unreadable_events += scopes is None
            for scope in scopes or []:
                app["scopes"].add(scope if _SCOPE.fullmatch(scope) else "other")
            if actor_key:
                app["actors"].add(actor_key)
    items = [
        {
            "client_id": client_id,
            "app_name": app_name,
            "events": dict(sorted(app["events"].items())),
            "scopes": sorted(app["scopes"]),
            "observed_users": len(app["actors"]),
        }
        for (client_id, app_name), app in sorted(
            apps.items(), key=lambda kv: (kv[0][0], kv[0][1] or "")
        )
    ]
    summary = {**_report_summary(window, activities), "activities_without_actor": without_actor}
    if unreadable_events:
        summary |= {"complete": False, "unreadable_events": unreadable_events}
    return Section(status=CollectionStatus.SUCCESS, items=items, summary=summary)


def _event_scopes(parameter: _Parameter | None) -> list[str] | None:
    """Scopes de un evento: `value` (separados por espacios) y/o `multiValue`; None si ilegible."""
    if parameter is None:
        return []
    if parameter.value is None and parameter.multi_value is None:
        return None
    return [*(parameter.value or "").split(), *(parameter.multi_value or [])]


def _event_name(name: str | None) -> str:
    return name if name and _EVENT_NAME.fullmatch(name) else "other"


def collect_admin_events(client: WorkspaceClient, now: datetime) -> Section:
    """Recuento por tipo y nombre de evento administrativo; nunca parámetros ni actores."""
    window = _window(now)
    status, reason, activities = _attempt(lambda: _activities(client, "admin", window))
    if activities is None:
        return _failed(status, reason, complete=False)
    counts = Counter(
        (_event_name(event.type), _event_name(event.name))
        for activity in activities
        for event in activity.events
    )
    items = [
        {"type": kind, "name": name, "count": count}
        for (kind, name), count in sorted(counts.items())
    ]
    return Section(
        status=CollectionStatus.SUCCESS, items=items, summary=_report_summary(window, activities)
    )
