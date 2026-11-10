"""Secciones del conector `keystore`: solo metadatos.

Cada sección se construye campo a campo con lo que los checks necesitan. Nunca se copia una
respuesta entera: `custom_metadata`, claves públicas de transit y `id` y `meta` de los tokens
no llegan a la evidencia.
"""

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from ens_evidence.connectors.keystore.client import (
    KeystoreClient,
    KeystoreError,
    NotFound,
    segment,
)
from ens_evidence.connectors.keystore.config import KeystoreRun
from ens_evidence.connectors.sections import Section
from ens_evidence.models.enums import CollectionStatus

APP_POLICY = "ens-app-metadata"
KV_MOUNT = "kv/"
TRANSIT_MOUNT = "transit/"
SYSTEM_MOUNT_TYPES = frozenset({"system", "cubbyhole", "identity"})
MAX_KV_REQUESTS = 2000
MAX_TRANSIT_KEYS = 500
MAX_ACCESSORS = 500
# rutas de valores que la política de la app niega: `capabilities-self` no lee nada
VALUE_PATHS = (
    "kv/data/ens-probe",
    "kv/subkeys/ens-probe",
    "kv/delete/ens-probe",
    "kv/destroy/ens-probe",
    "transit/export/encryption-key/ens-probe",
    "transit/backup/ens-probe",
    "transit/decrypt/ens-probe",
    "transit/encrypt/ens-probe",
    "transit/sign/ens-probe",
)


def instant(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


def parse_time(raw: Any) -> datetime | None:
    """Fecha RFC 3339 de OpenBao, o epoch en segundos; None si no es legible."""
    if isinstance(raw, bool):
        return None
    try:
        if isinstance(raw, int | float):
            return datetime.fromtimestamp(raw, UTC)
        if isinstance(raw, str):
            moment = datetime.fromisoformat(raw)
            return moment if moment.tzinfo else None
    except (ValueError, OverflowError, OSError):
        return None
    return None


def _int(raw: Any) -> int | None:
    return raw if isinstance(raw, int) and not isinstance(raw, bool) else None


def _dict(raw: Any) -> dict[str, Any]:
    return raw if isinstance(raw, dict) else {}


def _strings(raw: Any) -> list[str]:
    return sorted(s for s in raw if isinstance(s, str)) if isinstance(raw, list) else []


def guarded(read: Callable[[], Section]) -> Section:
    try:
        return read()
    except KeystoreError as exc:
        return Section(status=exc.status, reason=exc.message)


def unavailable(reason: str) -> Section:
    return Section(status=CollectionStatus.ERROR, reason=reason)


def collect_seal(client: KeystoreClient) -> Section:
    body = client.get("sys/seal-status").body
    summary = {
        "initialized": body.get("initialized"),
        "sealed": body.get("sealed"),
        "t": _int(body.get("t")),
        "n": _int(body.get("n")),
        "progress": _int(body.get("progress")),
        "version": body.get("version") if isinstance(body.get("version"), str) else None,
    }
    if not isinstance(summary["initialized"], bool) or not isinstance(summary["sealed"], bool):
        raise KeystoreError(CollectionStatus.ERROR, "seal-status sin `initialized` y `sealed`")
    return Section(status=CollectionStatus.SUCCESS, summary=summary)


def collect_token(client: KeystoreClient, now: datetime) -> Section:
    """`lookup-self`, renovación solo si TTL < periodo/2 y capacidades sobre rutas de valores."""
    lookup = _token_lookup(client)
    renewed = False
    period, ttl = lookup["period_seconds"], lookup["ttl_seconds"]
    if lookup["renewable"] and period and ttl is not None and ttl < period / 2:
        client.post("auth/token/renew-self", {})
        renewed = True
        lookup = _token_lookup(client)
    summary = {**lookup, "renewed": renewed, "capabilities": _capabilities(client)}
    return Section(status=CollectionStatus.SUCCESS, summary=summary)


def _token_lookup(client: KeystoreClient) -> dict[str, Any]:
    data = client.get("auth/token/lookup-self").data
    # OpenBao 2.7.2 no devuelve `period` en lookup-self (medido con un token del rol ens-app, que
    # sí es periódico): su `creation_ttl` es el periodo. Con `period` presente manda `period`.
    return {
        "policies": _strings(data.get("policies")),
        "period_seconds": _int(data.get("period")) or _int(data.get("creation_ttl")),
        "ttl_seconds": _int(data.get("ttl")),
        "renewable": data.get("renewable") is True,
        "orphan": data.get("orphan") is True,
    }


def _capabilities(client: KeystoreClient) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in VALUE_PATHS:
        reply = client.post("sys/capabilities-self", {"paths": [path]})
        raw = reply.data.get(path, reply.body.get(path, reply.data.get("capabilities")))
        found[path] = _strings(raw)
    return found


def collect_audit(client: KeystoreClient) -> Section:
    reply = client.get("sys/audit")
    devices = {**reply.body, **reply.data}
    items = [
        {"name": name.rstrip("/"), "type": device["type"]}
        for name, device in sorted(devices.items())
        if isinstance(device, dict) and isinstance(device.get("type"), str)
    ]
    return Section(status=CollectionStatus.SUCCESS, items=items, summary={"count": len(items)})


def collect_mounts(client: KeystoreClient) -> Section:
    reply = client.get("sys/mounts")
    mounts = {**reply.body, **reply.data}
    items = []
    for path, mount in sorted(mounts.items()):
        if not (isinstance(mount, dict) and isinstance(mount.get("type"), str)):
            continue
        version = _dict(mount.get("options")).get("version")
        items.append(
            {
                "path": path,
                "type": mount["type"],
                "kv_version": version if isinstance(version, str) else None,
                "covered": _covered(path, mount["type"], version),
            }
        )
    uncovered = [i["path"] for i in items if not i["covered"]]
    return Section(
        status=CollectionStatus.SUCCESS,
        items=items,
        summary={"count": len(items), "uncovered": uncovered},
    )


def _covered(path: str, kind: str, version: Any) -> bool:
    """Cubierto: lo del sistema, `kv/` v2 y `transit/`; cualquier otro motor no se lee."""
    if kind in SYSTEM_MOUNT_TYPES:
        return True
    if path == KV_MOUNT:
        return kind == "kv" and version == "2"
    return path == TRANSIT_MOUNT and kind == "transit"


def collect_kv(client: KeystoreClient) -> Section:
    items: list[dict[str, Any]] = []
    pending = [""]
    requests = 0
    while pending:
        folder = pending.pop()
        requests += 1
        if requests > MAX_KV_REQUESTS:
            raise KeystoreError(CollectionStatus.ERROR, "demasiadas entradas en kv: parcial")
        prefix = "".join(f"{segment(s)}/" for s in folder.split("/") if s)
        try:
            names = client.list(f"kv/metadata/{prefix}")
        except NotFound:  # LIST de un directorio vacío o inexistente
            continue
        for name in names:
            if name.endswith("/"):
                pending.append(f"{folder}{name}")
                continue
            requests += 1
            items.append(_kv_entry(client, f"{folder}{name}", prefix + segment(name)))
    items.sort(key=lambda i: i["path"])
    return Section(status=CollectionStatus.SUCCESS, items=items, summary={"count": len(items)})


def _kv_entry(client: KeystoreClient, path: str, quoted: str) -> dict[str, Any]:
    data = client.get(f"kv/metadata/{quoted}").data
    current = _int(data.get("current_version"))
    version = _dict(_dict(data.get("versions")).get(str(current)))
    created = parse_time(version.get("created_time"))
    return {
        "path": path,
        "current_version": current,
        "last_version_at": instant(created) if created else None,
    }


def collect_transit(client: KeystoreClient) -> Section:
    try:
        names = client.list("transit/keys/")
    except NotFound:
        names = []
    if len(names) > MAX_TRANSIT_KEYS:
        raise KeystoreError(CollectionStatus.ERROR, "demasiadas claves en transit: lectura parcial")
    items = [_transit_key(client, name) for name in sorted(names)]
    return Section(status=CollectionStatus.SUCCESS, items=items, summary={"count": len(items)})


def _transit_key(client: KeystoreClient, name: str) -> dict[str, Any]:
    data = client.get(f"transit/keys/{segment(name)}").data
    latest = _int(data.get("latest_version"))
    version = _dict(data.get("keys")).get(str(latest))
    # simétrica: epoch; asimétrica: objeto del que solo se lee `creation_time`
    created = parse_time(version.get("creation_time") if isinstance(version, dict) else version)
    return {
        "name": name,
        "type": data.get("type") if isinstance(data.get("type"), str) else None,
        "latest_version": latest,
        "last_version_at": instant(created) if created else None,
        "auto_rotate_period_seconds": _int(data.get("auto_rotate_period")),
        "exportable": data.get("exportable") is True,
        "allow_plaintext_backup": data.get("allow_plaintext_backup") is True,
    }


def collect_policies(client: KeystoreClient) -> Section:
    try:
        text = client.get(f"sys/policies/acl/{APP_POLICY}").data.get("policy")
    except NotFound:
        text = None
    if text is None:
        item = {"name": APP_POLICY, "exists": False, "sha256": None}
    elif isinstance(text, str):
        digest = hashlib.sha256(text.encode()).hexdigest()
        item = {"name": APP_POLICY, "exists": True, "sha256": digest}
    else:
        raise KeystoreError(CollectionStatus.ERROR, "política sin texto legible")
    return Section(status=CollectionStatus.SUCCESS, items=[item], summary={"count": 1})


def collect_accessors(client: KeystoreClient) -> Section:
    listed = client.list("auth/token/accessors")
    items: list[dict[str, Any]] = []
    unreadable = 0
    for accessor in listed[:MAX_ACCESSORS]:
        try:
            data = client.post("auth/token/lookup-accessor", {"accessor": accessor}).data
        except KeystoreError as exc:
            if exc.status is CollectionStatus.PERMISSION_DENIED:
                raise
            unreadable += 1
            continue
        items.append(
            {
                "policies": _strings(data.get("policies")),
                "orphan": data.get("orphan") is True,
                "ttl_seconds": _int(data.get("ttl")),
            }
        )
    summary = {
        "count": len(items),
        "listed": len(listed),
        "unreadable": unreadable,
        "truncated": len(listed) > MAX_ACCESSORS,
    }
    return Section(status=CollectionStatus.SUCCESS, items=items, summary=summary)


def collect_custodians(config: KeystoreRun) -> Section:
    """Solo recuento y fechas: los nombres de los custodios no entran en la evidencia."""
    register = config.custody
    unreadable = list(config.unreadable_registers)
    summary: dict[str, Any] = {"registered": register is not None, "unreadable": unreadable}
    if register is not None:
        review = register.review
        summary |= {
            "count": len(register.custodians),
            "key_shares": register.key_shares,
            "key_threshold": register.key_threshold,
            "reviewed_at": instant(review.reviewed_at),
            "valid_until": instant(review.valid_until),
        }
    return Section(status=CollectionStatus.SUCCESS, summary=summary)
