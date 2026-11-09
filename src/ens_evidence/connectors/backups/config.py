"""Configuración del conector `backups` (`connectors/backups.yaml`).

Sin secretos: las credenciales de cada repositorio salen del entorno,
`ENS_BACKUPS_<ID>_{RESTIC_PASSWORD,AWS_ACCESS_KEY_ID,AWS_SECRET_ACCESS_KEY}` con `<ID>` el
`id` en mayúsculas. Los umbrales son decisiones de la organización: no tienen valor por defecto.
"""

from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from ens_evidence.connectors.restic.config import Days, Offsite, WorkspaceRecords

RepositoryId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,39}$")]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RepositoryConfig(_Strict):
    id: RepositoryId
    asset_id: str = Field(min_length=1)
    repository: str = Field(min_length=1)
    max_backup_age_hours: Days
    max_check_age_days: Days
    restore_test_validity_days: Days
    # sin umbral, BACKUP-004 es UNKNOWN: la cobertura mínima la decide la organización
    min_check_read_data_percent: Annotated[int, Field(ge=0, le=100, strict=True)] | None = None
    offsite: Offsite | None = None

    @property
    def env_prefix(self) -> str:
        return f"ENS_BACKUPS_{self.id.upper()}_"


class BackupsConfig(_Strict):
    repositories: list[RepositoryConfig] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique(self) -> Self:
        ids = [r.id.upper() for r in self.repositories]
        if len(set(ids)) != len(ids):
            raise ValueError("repositories: `id` repetido (sin distinguir mayúsculas)")
        assets = [r.asset_id for r in self.repositories]
        if len(set(assets)) != len(assets):
            raise ValueError("repositories: `asset_id` repetido")
        return self


class BackupsRun(BackupsConfig):
    """Configuración más los registros del workspace que `prepare` resuelve para una recogida."""

    records: WorkspaceRecords = Field(default_factory=WorkspaceRecords)
