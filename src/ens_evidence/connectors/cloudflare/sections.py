"""Secciones normalizadas de la recogida.

Cada sección tiene su `CollectionStatus`: "no configurado" es `SUCCESS` con
`items: []`, distinto de `PERMISSION_DENIED` o `ERROR`. Solo se persisten los
campos de lista blanca; nada de lo demás que devuelva Cloudflare sale del parseo.
"""

import re
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ens_evidence.connectors.cloudflare.client import (
    AUDIT_LOGS_PAGER,
    DNS_PAGER,
    POLICIES_PAGER,
    RULESETS_PAGER,
    TUNNELS_PAGER,
    ZONES_PAGER,
    CloudflareClient,
    CloudflareError,
    CredentialRejected,
)
from ens_evidence.connectors.cloudflare.config import CloudflareConfig, CloudflareRun
from ens_evidence.models.enums import CollectionStatus

AUDIT_WINDOW = timedelta(days=30)
AUDIT_PAGE_SIZE = 1000
TLS_SETTINGS = ("ssl", "min_tls_version", "always_use_https")
CFARGOTUNNEL_SUFFIX = ".cfargotunnel.com"
_SEVERITY = (
    CollectionStatus.ERROR,
    CollectionStatus.PERMISSION_DENIED,
    CollectionStatus.NOT_SUPPORTED,
    CollectionStatus.SUCCESS,
)
_SELECTOR_KIND = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,40}$")
_BARE_SERVICES = frozenset(
    {"http_status", "unix", "unix+tls", "hello_world", "hello-world", "bastion", "socks"}
)


class Section(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: CollectionStatus
    reason: str | None = None
    items: list[dict[str, Any]] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)


class _Raw(BaseModel):
    """Forma mínima esperada de la API; lo que no se declara aquí se descarta."""

    model_config = ConfigDict(extra="ignore")


class _Zone(_Raw):
    id: str
    name: str
    status: str | None = None
    paused: bool | None = None
    type: str | None = None


class _Setting(_Raw):
    value: str


class _DnsRecord(_Raw):
    type: str
    name: str
    proxied: bool | None = None
    content: str | None = None


class _Ruleset(_Raw):
    id: str
    name: str | None = None
    kind: str | None = None
    phase: str | None = None


class _Destination(_Raw):
    type: str | None = None
    uri: str | None = None


class _AccessApp(_Raw):
    id: str
    name: str | None = None
    type: str | None = None
    domain: str | None = None
    self_hosted_domains: list[str] | None = None
    destinations: list[_Destination] | None = None


class _AccessPolicy(_Raw):
    id: str
    name: str | None = None
    decision: str | None = None
    precedence: int | None = None
    include: list[Any] = Field(default_factory=list)
    exclude: list[Any] = Field(default_factory=list)
    require: list[Any] = Field(default_factory=list)


class _Tunnel(_Raw):
    id: str
    name: str | None = None
    status: str | None = None
    config_src: str | None = None
    remote_config: bool | None = None


class _IngressRule(_Raw):
    hostname: str | None = None
    path: str | None = None
    service: str | None = None


class _IngressConfig(_Raw):
    ingress: list[_IngressRule] = Field(default_factory=list)


class _TunnelConfiguration(_Raw):
    config: _IngressConfig | None = None


def worst(statuses: Iterable[CollectionStatus]) -> CollectionStatus:
    present = set(statuses)
    return next((status for status in _SEVERITY if status in present), CollectionStatus.SUCCESS)


def _attempt[T](fetch: Callable[[], T]) -> tuple[CollectionStatus, str | None, T | None]:
    """Ejecuta una lectura; un fallo saneado se convierte en estado y motivo."""
    try:
        return CollectionStatus.SUCCESS, None, fetch()
    except CredentialRejected:
        raise
    except CloudflareError as exc:
        return exc.status, exc.message, None
    except (ValidationError, KeyError, TypeError, ValueError, AttributeError):
        return CollectionStatus.ERROR, "respuesta con forma inesperada", None


def _failed(status: CollectionStatus, reason: str | None) -> Section:
    return Section(status=status, reason=reason)


def _dependent_on(parent: Section, label: str) -> Section | None:
    if parent.status is CollectionStatus.SUCCESS:
        return None
    return _failed(parent.status, f"depende de {label}: {parent.reason or parent.status.value}")


def _per_item(
    parents: list[dict[str, Any]],
    key: str,
    fetch: Callable[[str], dict[str, Any]],
) -> Section:
    items: list[dict[str, Any]] = []
    for parent in parents:
        status, reason, payload = _attempt(lambda parent=parent: fetch(parent["id"]))
        entry: dict[str, Any] = {key: parent["id"], "status": status}
        if reason:
            entry["reason"] = reason
        items.append({**entry, **(payload or {})})
    return Section(status=worst(item["status"] for item in items), items=items)


def selector_kinds(rules: list[Any]) -> list[str]:
    """Tipos de selector de una regla de Access (`email`, `everyone`...), sin sus valores."""
    kinds: list[str] = []
    for rule in rules:
        key = next(iter(rule)) if isinstance(rule, dict) and len(rule) == 1 else None
        kind = key if isinstance(key, str) and _SELECTOR_KIND.match(key) else "unknown"
        if kind not in kinds:
            kinds.append(kind)
    return kinds


def sanitize_service(service: str | None) -> str | None:
    """`esquema://host[:puerto]` sin credenciales, ruta ni query."""
    if service is None:
        return None
    try:
        parts = urlsplit(service)
        host, port = parts.hostname, parts.port
    except ValueError:
        return "other"
    if host and parts.scheme:
        shown = f"[{host}]" if ":" in host else host
        return f"{parts.scheme}://{shown}" + (f":{port}" if port else "")
    head = service.split(":", 1)[0]
    return head if head in _BARE_SERVICES else "other"


def collect_zones(client: CloudflareClient, config: CloudflareRun) -> Section:
    status, reason, zones = _attempt(
        lambda: [
            _Zone.model_validate(raw)
            for raw in client.pages("/zones", ZONES_PAGER, **{"account.id": config.account_id})
        ]
    )
    if zones is None:
        return _failed(status, reason)
    seen = {zone.name.lower() for zone in zones}
    expected = {name.lower() for name in config.inventory_zones}
    return Section(
        status=CollectionStatus.SUCCESS,
        items=[zone.model_dump() for zone in zones],
        summary={
            "missing_from_token": sorted(expected - seen),
            "not_in_inventory": sorted(seen - expected),
        },
    )


def collect_dns(client: CloudflareClient, zones: Section) -> Section:
    def fetch(zone_id: str) -> dict[str, Any]:
        records = [
            _DnsRecord.model_validate(raw)
            for raw in client.pages(f"/zones/{zone_id}/dns_records", DNS_PAGER)
        ]
        return {
            "records": [
                {
                    "type": record.type,
                    "name": record.name,
                    "proxied": record.proxied,
                    **({"content": record.content} if record.type == "CNAME" else {}),
                }
                for record in records
            ]
        }

    return _dependent_on(zones, "zones") or _per_item(zones.items, "zone_id", fetch)


def collect_tls(client: CloudflareClient, zones: Section) -> Section:
    def fetch(zone_id: str) -> dict[str, Any]:
        settings: dict[str, Any] = {}
        for name in TLS_SETTINGS:
            status, reason, value = _attempt(
                lambda name=name: (
                    _Setting.model_validate(
                        client.get(f"/zones/{zone_id}/settings/{name}").result
                    ).value
                )
            )
            settings[name] = {"status": status, "value": value}
            if reason:
                settings[name]["reason"] = reason
        return {"settings": settings}

    if blocked := _dependent_on(zones, "zones"):
        return blocked
    section = _per_item(zones.items, "zone_id", fetch)
    for item in section.items:
        item["status"] = worst(setting["status"] for setting in item["settings"].values())
    return section.model_copy(update={"status": worst(item["status"] for item in section.items)})


def collect_waf(client: CloudflareClient, zones: Section) -> Section:
    def fetch(zone_id: str) -> dict[str, Any]:
        rulesets = [
            _Ruleset.model_validate(raw)
            for raw in client.pages(f"/zones/{zone_id}/rulesets", RULESETS_PAGER)
        ]
        return {"rulesets": [ruleset.model_dump() for ruleset in rulesets]}

    return _dependent_on(zones, "zones") or _per_item(zones.items, "zone_id", fetch)


def public_destinations(app: _AccessApp) -> list[str]:
    """Destinos públicos efectivos: `destinations`, si no `self_hosted_domains`, si no `domain`."""
    if app.destinations:
        return [d.uri for d in app.destinations if d.type == "public" and d.uri]
    if app.self_hosted_domains:
        return list(app.self_hosted_domains)
    return [app.domain] if app.domain else []


def collect_access_applications(client: CloudflareClient, config: CloudflareConfig) -> Section:
    status, reason, apps = _attempt(
        lambda: [
            _AccessApp.model_validate(raw)
            for raw in client.pages(f"/accounts/{config.account_id}/access/apps")
        ]
    )
    if apps is None:
        return _failed(status, reason)
    return Section(
        status=CollectionStatus.SUCCESS,
        items=[
            {
                "id": app.id,
                "name": app.name,
                "type": app.type,
                "public_destinations": public_destinations(app),
            }
            for app in apps
        ],
    )


def collect_access_policies(
    client: CloudflareClient, config: CloudflareConfig, applications: Section
) -> Section:
    """Políticas de cada aplicación con destinos públicos (las demás no afectan a rutas)."""

    def fetch(app_id: str) -> dict[str, Any]:
        policies = [
            _AccessPolicy.model_validate(raw)
            for raw in client.pages(
                f"/accounts/{config.account_id}/access/apps/{app_id}/policies", POLICIES_PAGER
            )
        ]
        return {
            "policies": [
                {
                    "id": policy.id,
                    "name": policy.name,
                    "decision": policy.decision,
                    "precedence": policy.precedence,
                    "include": selector_kinds(policy.include),
                    "exclude": selector_kinds(policy.exclude),
                    "require": selector_kinds(policy.require),
                }
                for policy in policies
            ]
        }

    if blocked := _dependent_on(applications, "access_applications"):
        return blocked
    exposed = [app for app in applications.items if app["public_destinations"]]
    return _per_item(exposed, "app_id", fetch)


def collect_tunnels(client: CloudflareClient, config: CloudflareConfig, dns: Section) -> Section:
    status, reason, tunnels = _attempt(
        lambda: [
            _Tunnel.model_validate(raw)
            for raw in client.pages(
                f"/accounts/{config.account_id}/cfd_tunnel", TUNNELS_PAGER, is_deleted="false"
            )
        ]
    )
    if tunnels is None:
        return _failed(status, reason)
    items = [_tunnel_item(client, config, tunnel, dns) for tunnel in tunnels]
    relevant = (
        item["ingress_status"]
        for item in items
        if item["ingress_status"] is not CollectionStatus.NOT_SUPPORTED
    )
    return Section(status=worst(relevant), items=items)


def _is_remote(tunnel: _Tunnel) -> bool:
    return tunnel.config_src == "cloudflare" or (
        tunnel.config_src is None and tunnel.remote_config is True
    )


def _tunnel_item(
    client: CloudflareClient, config: CloudflareConfig, tunnel: _Tunnel, dns: Section
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": tunnel.id,
        "name": tunnel.name,
        "status": tunnel.status,
        "config_src": tunnel.config_src,
        "ingress": [],
        "inferred_complete": False,
    }
    if not _is_remote(tunnel):
        inferred = _inferred_hostnames(tunnel.id, dns)
        item["ingress_status"] = CollectionStatus.NOT_SUPPORTED
        item["reason"] = "ingress local no visible por la API"
        if inferred is not None:
            item["inferred_complete"] = True
            item["ingress"] = [
                {"hostname": host, "path": None, "service": None, "inferred": True}
                for host in inferred
            ]
        return item
    status, reason, ingress = _attempt(
        lambda: _remote_ingress(client, config.account_id, tunnel.id)
    )
    item["ingress_status"] = status
    if reason:
        item["reason"] = reason
    item["ingress"] = ingress or []
    return item


def _remote_ingress(client: CloudflareClient, account_id: str, tunnel_id: str) -> list[Any]:
    path = f"/accounts/{account_id}/cfd_tunnel/{tunnel_id}/configurations"
    configuration = _TunnelConfiguration.model_validate(client.get(path).result)
    rules = configuration.config.ingress if configuration.config else []
    return [
        {
            "hostname": rule.hostname,
            "path": rule.path,
            "service": sanitize_service(rule.service),
            "inferred": False,
        }
        for rule in rules
    ]


def _inferred_hostnames(tunnel_id: str, dns: Section) -> list[str] | None:
    """Hostnames con CNAME al túnel; `None` si el DNS no se recogió entero."""
    if dns.status is not CollectionStatus.SUCCESS:
        return None
    target = f"{tunnel_id}{CFARGOTUNNEL_SUFFIX}".lower()
    return sorted(
        {
            record["name"]
            for zone in dns.items
            for record in zone["records"]
            if record["type"] == "CNAME" and str(record.get("content", "")).lower() == target
        }
    )


def collect_audit_logs(
    client: CloudflareClient, config: CloudflareConfig, now: datetime
) -> Section:
    """Solo recuento y ventana observada; los eventos no se guardan. Cero eventos no prueba nada."""
    since = now - AUDIT_WINDOW
    summary: dict[str, Any] = {
        "requested_since": _instant(since),
        "requested_before": _instant(now),
        "complete": False,
    }
    status, reason, entries = _attempt(
        lambda: client.pages(
            f"/accounts/{config.account_id}/logs/audit",
            AUDIT_LOGS_PAGER,
            since=_instant(since),
            before=_instant(now),
            limit=AUDIT_PAGE_SIZE,
            direction="desc",
        )
    )
    if entries is None:
        return Section(status=status, reason=reason, summary=summary)
    times = sorted(t for t in map(_entry_time, entries) if t is not None)
    summary |= {
        "complete": True,
        "count": len(entries),
        "observed_first": _instant(times[0]) if times else None,
        "observed_last": _instant(times[-1]) if times else None,
    }
    return Section(status=CollectionStatus.SUCCESS, summary=summary)


def _instant(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _entry_time(entry: Any) -> datetime | None:
    if not isinstance(entry, dict):
        return None
    action = entry.get("action")
    raw = (action.get("time") if isinstance(action, dict) else None) or entry.get("when")
    if not isinstance(raw, str):
        return None
    try:
        moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)
