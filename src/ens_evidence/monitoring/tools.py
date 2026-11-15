"""Herramientas del kit: disponibilidad, dato clave y seguridad, tres señales separadas.

Disponibilidad: OpenBao por HTTP (`sys/seal-status`); el resto, por su contenedor del proyecto de
Compose. Dato clave: la última evidencia del conector (mismo juicio que `kit_state` de la UI, vía
`kit.state.judge_kit`). Seguridad: solo avisos de GitHub.
"""

import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import httpx

from ens_evidence.agent.monitoring import Sample
from ens_evidence.checks.store import scan_runs
from ens_evidence.connectors.keystore.client import KeystoreClient, KeystoreError
from ens_evidence.kit.registry import (
    CLAVES_IMAGE,
    CONTRASENAS_IMAGE,
    COPIAS_IMAGE,
    KIT_TOOLS,
    KitTool,
)
from ens_evidence.kit.state import judge_kit
from ens_evidence.monitoring.advisories import AdvisoryFeed, judge
from ens_evidence.monitoring.model import GROUP_TOOLS, Reading, Status, is_fresh, unknown
from ens_evidence.monitoring.vps import Samples, container_status, project_containers
from ens_evidence.workspace import WorkspaceStore

DEFAULT_OPENBAO = "http://openbao:8200"
KEY_DATUM_TTL = timedelta(hours=26)
_KEY_DATUM = {
    "sealed": (Status.FAIL, "El almacén está sellado."),
    "nodata": (Status.UNKNOWN, "La comprobación no incluye checks de esta herramienta."),
    "down": (Status.WARN, "La app no ha podido leer la herramienta en la última recogida."),
    "ok": (Status.OK, "Todas las comprobaciones de la herramienta pasan."),
    "problems": (Status.FAIL, "Alguna comprobación de la herramienta falla."),
}
SERVICE_BY_TOOL = {"copias": "backrest", "claves": "openbao", "contrasenas": "vaultwarden"}
PINNED_IMAGE = {"copias": COPIAS_IMAGE, "claves": CLAVES_IMAGE, "contrasenas": CONTRASENAS_IMAGE}
GITHUB_REPO = {
    "copias": "garethgeorge/backrest",
    "claves": "openbao/openbao",
    "contrasenas": "dani-garcia/vaultwarden",
}
_TAG_VERSION = re.compile(r":v?(\d+(?:\.\d+)+)")


@dataclass(frozen=True)
class OpenBaoSeal:
    sealed: bool
    version: str | None


def probe_openbao(address: str, transport: httpx.BaseTransport | None = None) -> OpenBaoSeal | None:
    """`sys/seal-status` sin token por el cliente de la lista blanca; `None` si no responde."""
    try:
        with KeystoreClient(address, None, transport=transport) as client:
            body = client.get("sys/seal-status").body
    except KeystoreError:
        return None
    sealed, initialized = body.get("sealed"), body.get("initialized")
    if not isinstance(sealed, bool) or not isinstance(initialized, bool):
        return None
    version = body.get("version")
    return OpenBaoSeal(sealed or not initialized, version if isinstance(version, str) else None)


def image_version(image: str) -> str | None:
    match = _TAG_VERSION.search(image.split("@")[0])
    return match.group(1) if match else None


def matches_pin(tool_id: str, image: str) -> bool:
    return image.split("@")[0] == PINNED_IMAGE[tool_id].split("@")[0]


def _title(tool: KitTool, column: str) -> str:
    return f"{tool.name} · {column}"


def _availability(
    tool: KitTool,
    containers: dict[str, dict[str, str]] | None,
    seal: OpenBaoSeal | None,
    now: datetime,
    ttl_ok: bool,
) -> Reading:
    id, title = f"tool:{tool.id}:up", _title(tool, "Disponibilidad")
    if tool.id == "claves":
        if seal is None:
            return Reading(
                id,
                GROUP_TOOLS,
                title,
                Status.FAIL,
                "OpenBao no responde.",
                tool.help_slug,
                observed_at=now,
            )
        status, detail = (
            (Status.FAIL, "OpenBao responde pero está sellado o sin inicializar.")
            if seal.sealed
            else (Status.OK, "OpenBao responde y está desbloqueado.")
        )
        return Reading(id, GROUP_TOOLS, title, status, detail, tool.help_slug, observed_at=now)
    if containers is None or not ttl_ok:
        return unknown(
            id, GROUP_TOOLS, title, "Sin dato reciente del agente del VPS.", tool.help_slug
        )
    row = containers.get(SERVICE_BY_TOOL[tool.id])
    if row is None:  # muestra vigente y válida, y la herramienta esperada no está: se ha caído
        return Reading(
            id,
            GROUP_TOOLS,
            title,
            Status.FAIL,
            "El contenedor de la herramienta ya no existe en el proyecto.",
            tool.help_slug,
            observed_at=now,
        )
    status, detail = container_status(row)
    return Reading(
        id, GROUP_TOOLS, title, status, detail.capitalize() + ".", tool.help_slug, observed_at=now
    )


def key_datum(store: WorkspaceStore, tool: KitTool, now: datetime) -> Reading:
    """Estado de la última comprobación guardada del conector de la herramienta."""
    id, title = f"tool:{tool.id}:key", _title(tool, "Dato clave")
    judgment = judge_kit(store, tool.connector, tool.check_ids)
    at = judgment.checked_at
    if at is None:
        why = "evidencia ilegible" if judgment.key == "problems" else "aún sin comprobación"
        return unknown(id, GROUP_TOOLS, title, f"Sin dato: {why}.", tool.help_slug)
    if not is_fresh(at, KEY_DATUM_TTL, now):
        return Reading(
            id,
            GROUP_TOOLS,
            title,
            Status.UNKNOWN,
            "La última comprobación ha vencido (más de 26 h).",
            tool.help_slug,
            observed_at=at,
        )
    status, detail = _KEY_DATUM[judgment.key]
    return Reading(id, GROUP_TOOLS, title, status, detail, tool.help_slug, observed_at=at)


def _installed_version(
    tool: KitTool, containers: dict[str, dict[str, str]] | None, seal: OpenBaoSeal | None
) -> tuple[str | None, str | None]:
    """(versión instalada, advertencia). OpenBao da la suya por HTTP; el resto, la imagen."""
    image = (containers or {}).get(SERVICE_BY_TOOL[tool.id], {}).get("image")
    version = seal.version if tool.id == "claves" and seal else None
    if image:
        version = version or image_version(image)
        if not matches_pin(tool.id, image):
            return version, "La imagen en marcha no es la fijada en el paquete."
    return version, None


def security(
    tool: KitTool,
    feed: AdvisoryFeed,
    containers: dict[str, dict[str, str]] | None,
    seal: OpenBaoSeal | None,
    now: datetime,
) -> Reading:
    id, title = f"tool:{tool.id}:security", _title(tool, "Seguridad")
    repo = GITHUB_REPO[tool.id]
    data = feed.get(repo, now)
    if data is None:
        old = feed.reference(repo, now)
        past = (
            f" Referencia, no vigente: consulta del {old.fetched_at:%Y-%m-%d %H:%M} UTC."
            if old
            else ""
        )
        return unknown(
            id,
            GROUP_TOOLS,
            title,
            "La consulta diaria de avisos de GitHub ha fallado; no se asume que no haya." + past,
            tool.help_slug,
        )
    version, warning = _installed_version(tool, containers, seal)
    verdict, detail = judge(version, data)
    newer = (
        f" Última release publicada: {data.latest_release}."
        if data.latest_release and version and version not in data.latest_release
        else ""
    )
    status = {"fail": Status.FAIL, "review": Status.WARN, "clear": Status.OK}[verdict]
    if warning:
        status, detail = (Status.WARN if status is Status.OK else status), f"{warning} {detail}"
    return Reading(
        id, GROUP_TOOLS, title, status, detail + newer, tool.help_slug, observed_at=data.fetched_at
    )


def enabled_profiles() -> frozenset[str] | None:
    """Perfiles activos del despliegue; `None` si la caja no los publica."""
    raw = os.environ.get("ENS_KIT_PROFILES")
    return None if raw is None else frozenset(p.strip() for p in raw.split(",") if p.strip())


def tool_present(
    store: WorkspaceStore,
    tool: KitTool,
    containers: dict[str, Any] | None,
    seal: OpenBaoSeal | None,
    seen: frozenset[str] = frozenset(),
) -> bool:
    """La herramienta se espera en esta caja.

    Con `ENS_KIT_PROFILES` (perfiles de Compose activos, escritos por `deploy/box.sh`) manda la
    configuración del despliegue. Sin ella (desarrollo, despliegues antiguos), señales
    persistentes: evidencia del conector o disponibilidad ya vista en el histórico de 30 días
    (`seen`); si no, el contenedor o (OpenBao) la respuesta del almacén.
    """
    profiles = enabled_profiles()
    if profiles is not None:
        return tool.id in profiles  # el perfil de Compose de cada herramienta es su id
    if scan_runs(store, tool.connector).last(tool.connector) is not None:
        return True
    if f"tool:{tool.id}:up" in seen:
        return True
    if tool.id == "claves" and seal is not None:
        return True
    return SERVICE_BY_TOOL[tool.id] in (containers or {})


def tool_readings(
    store: WorkspaceStore,
    samples: Samples,
    feed: AdvisoryFeed,
    project: str,
    seal: OpenBaoSeal | None,
    now: datetime,
    seen: frozenset[str] = frozenset(),
) -> list[Reading]:
    containers = project_containers(samples, project, now)
    sample: Sample | None = samples.get("mon_containers")
    observed_ok = sample is not None and is_fresh(sample.observed_at, timedelta(minutes=45), now)
    readings: list[Reading] = []
    for tool in KIT_TOOLS:
        if tool.id not in SERVICE_BY_TOOL or not tool.on_box:
            continue
        if not tool_present(store, tool, containers, seal, seen):
            continue
        readings += [
            _availability(
                tool, containers, seal if tool.id == "claves" else None, now, observed_ok
            ),
            key_datum(store, tool, now),
            security(tool, feed, containers, seal if tool.id == "claves" else None, now),
        ]
    return readings
