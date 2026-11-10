"""Conector `keystore`: lee OpenBao con el token de la app, solo metadatos.

Un `CollectionResult` con una sección por tema. Si OpenBao no responde o está sellado, las
secciones que necesitan leer quedan en `ERROR`: nunca hay PASS sin lectura (regla 5).
"""

import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime

import httpx
from pydantic import BaseModel

from ens_evidence.connectors.base import Connector
from ens_evidence.connectors.keystore import sections
from ens_evidence.connectors.keystore.client import KeystoreClient
from ens_evidence.connectors.keystore.config import KeystoreConfig, KeystoreRun
from ens_evidence.connectors.sections import Section, worst
from ens_evidence.kit.custody import latest_register
from ens_evidence.models.contracts import CollectionResult
from ens_evidence.models.enums import CollectionStatus
from ens_evidence.scope import load_assets
from ens_evidence.scope.models import AssetType
from ens_evidence.workspace import WorkspaceError, WorkspaceStore

TOKEN_ENV = "ENS_KEYSTORE_TOKEN"
SOURCE = "keystore"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class KeystoreConnector(Connector):
    name = SOURCE
    config_model = KeystoreConfig

    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], datetime] = _utcnow,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._transport = transport
        self._clock = clock
        self._env = os.environ if env is None else env

    def prepare(self, config: BaseModel, store: WorkspaceStore) -> BaseModel:
        """Valida el activo `application` y lee el registro de custodios vigente."""
        assert isinstance(config, KeystoreConfig)
        assets, errors = load_assets(store)
        if assets is None:
            raise WorkspaceError("No se puede leer el inventario de activos: " + "; ".join(errors))
        asset = next((a for a in assets.assets if a.id == config.asset_id), None)
        if asset is None or asset.type is not AssetType.APPLICATION:
            raise WorkspaceError(
                f"asset_id: debe ser un activo application del inventario ({config.asset_id})."
            )
        register, unreadable = latest_register(store, self._clock())
        return KeystoreRun(**config.model_dump(), custody=register, unreadable_registers=unreadable)

    def collect(self, config: BaseModel) -> CollectionResult:
        assert isinstance(config, KeystoreRun)
        now = self._clock()
        token = self._env.get(TOKEN_ENV, "").strip()
        valid = bool(token) and token.isascii() and token.isprintable()
        with KeystoreClient(
            config.address, token if valid else None, transport=self._transport
        ) as client:
            collected = self._collect(client, config, now, token_problem=_token_problem(token))
        return CollectionResult(
            source=SOURCE,
            collector=self.name,
            collected_at=now,
            asset_id=config.asset_id,
            data={
                "thresholds": {
                    "max_key_age_days": config.max_key_age_days,
                    "max_token_period_hours": config.max_token_period_hours,
                },
                "sections": {name: s.model_dump(mode="json") for name, s in collected.items()},
            },
            collection_status=worst(s.status for s in collected.values()),
        )

    @staticmethod
    def _collect(
        client: KeystoreClient, config: KeystoreRun, now: datetime, token_problem: str | None
    ) -> dict[str, Section]:
        seal = sections.guarded(lambda: sections.collect_seal(client))
        collected = {"seal": seal}
        blocked = _blocked(seal, token_problem)
        if blocked:
            for name in ("token", "audit", "mounts", "kv", "transit", "policies", "accessors"):
                collected[name] = sections.unavailable(blocked)
        else:
            reads = {
                "token": lambda: sections.collect_token(client, now),
                "audit": lambda: sections.collect_audit(client),
                "mounts": lambda: sections.collect_mounts(client),
                "kv": lambda: sections.collect_kv(client),
                "transit": lambda: sections.collect_transit(client),
                "policies": lambda: sections.collect_policies(client),
                "accessors": lambda: sections.collect_accessors(client),
            }
            for name, read in reads.items():
                collected[name] = sections.guarded(read)
        collected["custodians"] = sections.collect_custodians(config)
        return collected


def _token_problem(token: str) -> str | None:
    if not token:
        return f"{TOKEN_ENV} no está definida"
    if not (token.isascii() and token.isprintable()):
        return f"{TOKEN_ENV} contiene caracteres no válidos"
    return None


def _blocked(seal: Section, token_problem: str | None) -> str | None:
    """Por qué no se pueden leer las demás secciones, o None."""
    if seal.status is not CollectionStatus.SUCCESS:
        return f"sin lectura de seal-status: {seal.reason}"
    if seal.summary["initialized"] is False:
        return "OpenBao sin inicializar: no hay nada que leer"
    if seal.summary["sealed"]:
        return "OpenBao sellado: no se puede leer hasta que los custodios lo desbloqueen"
    return token_problem
