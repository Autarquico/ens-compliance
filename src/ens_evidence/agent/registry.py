"""Registro de agentes: `agents/registry.yaml` en el workspace, así entra en la copia.

Se lee en fresco en cada petición; solo se escribe con el CycleLock tomado (lo hace la CLI).
"""

from datetime import datetime

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ens_evidence.endpoint.config import EndpointConfig
from ens_evidence.workspace import NotFound, WorkspaceError, WorkspaceStore
from ens_evidence.workspace.cycle_lock import CycleLock
from ens_evidence.yaml_unique import safe_load_unique

REGISTRY_KEY = "agents/registry.yaml"


class RegistryError(WorkspaceError):
    """El registro no se puede leer o escribir; el mensaje no incluye valores."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentRecord(_Strict):
    generation: int = Field(default=1, ge=1)
    revoked_at: datetime | None = None
    revoked_by: str | None = None
    revoked_reason: str | None = None
    last_admission_at: datetime | None = None
    last_admission_by: str | None = None
    last_admission_expires_at: datetime | None = None

    @property
    def revoked(self) -> bool:
        return self.revoked_at is not None


class AgentRegistry(_Strict):
    agents: dict[str, AgentRecord] = Field(default_factory=dict)

    def accepts(self, asset_id: str, generation: int) -> bool:
        """El node_key de esta generación sigue vigente: admitido, no revocado, misma generación."""
        record = self.agents.get(asset_id)
        return record is not None and not record.revoked and record.generation == generation


def load_registry(store: WorkspaceStore) -> AgentRegistry:
    try:
        raw = safe_load_unique(store.read_text(REGISTRY_KEY))
    except NotFound:
        return AgentRegistry()
    except (WorkspaceError, yaml.YAMLError):
        raise RegistryError(f"{REGISTRY_KEY}: no es YAML UTF-8 válido") from None
    try:
        return AgentRegistry.model_validate(raw or {})
    except ValidationError:
        raise RegistryError(f"{REGISTRY_KEY}: no cumple el esquema") from None


def record_admission(
    store: WorkspaceStore,
    lock: CycleLock,
    asset_id: str,
    *,
    by: str,
    now: datetime,
    expires_at: datetime,
) -> AgentRecord:
    """Abre (o reabre tras una revocación) el alta del activo; devuelve su registro."""

    def apply(registry: AgentRegistry) -> AgentRecord:
        record = registry.agents.get(asset_id) or AgentRecord()
        record = record.model_copy(
            update={
                "revoked_at": None,
                "revoked_by": None,
                "revoked_reason": None,
                "last_admission_at": now,
                "last_admission_by": by,
                "last_admission_expires_at": expires_at,
            }
        )
        registry.agents[asset_id] = record
        return record

    return _update(store, lock, apply)


def record_revocation(
    store: WorkspaceStore,
    lock: CycleLock,
    asset_id: str,
    *,
    by: str,
    reason: str,
    now: datetime,
) -> AgentRecord:
    """Sube la generación e invalida todos los node_key y tokens anteriores."""

    def apply(registry: AgentRegistry) -> AgentRecord:
        record = registry.agents.get(asset_id) or AgentRecord()
        record = record.model_copy(
            update={
                "generation": record.generation + 1,
                "revoked_at": now,
                "revoked_by": by,
                "revoked_reason": reason,
            }
        )
        registry.agents[asset_id] = record
        return record

    return _update(store, lock, apply)


def enrolled_asset(config: EndpointConfig, hardware_uuid: str, hostname: str) -> str | None:
    """Activo inscrito en `connectors/endpoint.yaml` cuyo uuid y nombre coinciden."""
    for asset_id, host in config.hosts.items():
        if host.matches(hardware_uuid, hostname):
            return asset_id
    return None


def _update(store: WorkspaceStore, lock: CycleLock, apply) -> AgentRecord:
    if not lock.held:
        raise RuntimeError("escribir el registro exige el CycleLock adquirido")
    registry = load_registry(store)
    record = apply(registry)
    store.write_text(
        REGISTRY_KEY,
        yaml.safe_dump(registry.model_dump(mode="json"), sort_keys=True, allow_unicode=True),
    )
    return record
