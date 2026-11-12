"""Conector `passwords`: lee el export de usuarios del gestor de contraseñas, solo lectura.

La app no tiene credenciales de Vaultwarden ni ve su base: solo el JSON del exportador. Export
ausente, viejo, ilegible, inválido o fechado en el futuro deja la recogida en `ERROR`, sin datos
de usuarios: nunca hay PASS sin lectura (regla 5). Nada del fichero rechazado se conserva.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel

from ens_evidence.connectors.base import Connector
from ens_evidence.connectors.passwords import reader
from ens_evidence.connectors.passwords.config import PasswordsConfig
from ens_evidence.kit.passwords_export import PasswordsExport
from ens_evidence.models.contracts import CollectionResult
from ens_evidence.models.enums import CollectionStatus
from ens_evidence.scope import load_assets
from ens_evidence.scope.models import AssetType
from ens_evidence.workspace import WorkspaceError, WorkspaceStore

SOURCE = "passwords"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class PasswordsConnector(Connector):
    name = SOURCE
    config_model = PasswordsConfig

    def __init__(self, *, clock: Callable[[], datetime] = _utcnow) -> None:
        self._clock = clock

    def prepare(self, config: BaseModel, store: WorkspaceStore) -> BaseModel:
        assert isinstance(config, PasswordsConfig)
        assets, errors = load_assets(store)
        if assets is None:
            raise WorkspaceError("No se puede leer el inventario de activos: " + "; ".join(errors))
        asset = next((a for a in assets.assets if a.id == config.asset_id), None)
        if asset is None or asset.type is not AssetType.APPLICATION:
            raise WorkspaceError(
                f"asset_id: debe ser un activo application del inventario ({config.asset_id})."
            )
        return config

    def collect(self, config: BaseModel) -> CollectionResult:
        assert isinstance(config, PasswordsConfig)
        now = self._clock()
        data: dict = {
            "thresholds": {
                "inactive_days": config.inactive_days,
                "max_export_age_minutes": config.max_export_age_minutes,
            }
        }
        try:
            export = reader.read_export(config.export_path)
            _require_fresh(export, now, timedelta(minutes=config.max_export_age_minutes))
        except reader.ReadFailure as failure:
            data["error"] = failure.code
            status = CollectionStatus.ERROR
        else:
            data["export"] = _summary(export, now)
            data["users"] = [_user(u) for u in export.users]
            status = CollectionStatus.SUCCESS
        return CollectionResult(
            source=SOURCE,
            collector=self.name,
            collected_at=now,
            asset_id=config.asset_id,
            data=data,
            collection_status=status,
        )


def _require_fresh(export: PasswordsExport, now: datetime, maximum: timedelta) -> None:
    age = now - export.exported_at
    if age < timedelta(0):
        raise reader.ReadFailure(reader.FUTURE)
    if age > maximum:
        raise reader.ReadFailure(reader.STALE)


def _summary(export: PasswordsExport, now: datetime) -> dict:
    """Solo lo que los checks necesitan; la migración de Vaultwarden no se copia."""
    return {
        "exported_at": export.exported_at.isoformat(),
        "age_minutes": (now - export.exported_at) // timedelta(minutes=1),
        "snapshot_ok": export.snapshot_ok,
        "last_good_snapshot_at": _iso(export.last_good_snapshot_at),
    }


def _user(user) -> dict:
    return {
        "id": str(user.id),
        "enabled": user.enabled,
        "invited": user.invited,
        "second_factor": user.second_factor,
        "created_at": user.created_at.isoformat(),
        "last_active": _iso(user.last_active),
    }


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None
