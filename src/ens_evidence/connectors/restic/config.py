"""Configuración del conector de Restic (`connectors/restic.yaml`).

Los umbrales son decisiones de la organización: no tienen valor por defecto.
El repositorio y los secretos salen de los ajustes `backup.*` y de `ENS_BACKUP_*`.
"""

from typing import Annotated, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from ens_evidence.backup.records import (
    BackupRun,
    ImmutabilityCheck,
    IntegrityCheck,
    RestoreRecord,
)
from ens_evidence.backup.restore_test import RestoreTest
from ens_evidence.models.enums import Role

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
Days = Annotated[int, Field(gt=0, strict=True)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Offsite(_Strict):
    """Declaración revisada por una persona de que el destino está fuera y separado."""

    location: Text
    administrative_separation: Text
    reviewer: Text
    role: Role
    reviewed_at: AwareDatetime
    valid_until: AwareDatetime

    @model_validator(mode="after")
    def _expires_after_review(self) -> Self:
        if self.valid_until <= self.reviewed_at:
            raise ValueError("valid_until debe ser posterior a reviewed_at")
        return self


class ResticConfig(_Strict):
    asset_id: str = Field(min_length=1)
    max_backup_age_hours: Days
    max_check_age_days: Days
    restore_test_validity_days: Days
    min_check_read_data_percent: Annotated[int, Field(ge=0, le=100, strict=True)]
    offsite: Offsite | None = None


class WorkspaceRecords(_Strict):
    """Registros del workspace leídos por `prepare`; los ilegibles se listan, no se omiten."""

    runs: list[BackupRun] = Field(default_factory=list)
    checks: list[IntegrityCheck] = Field(default_factory=list)
    restores: list[RestoreRecord] = Field(default_factory=list)
    restore_tests: list[RestoreTest] = Field(default_factory=list)
    # ids de restore tests sin evidencia manual propia o con `system` fuera del inventario
    without_manual_evidence: list[str] = Field(default_factory=list)
    without_inventoried_system: list[str] = Field(default_factory=list)
    immutability: list[ImmutabilityCheck] = Field(default_factory=list)
    unreadable: dict[str, list[str]] = Field(default_factory=dict)


class ResticRun(ResticConfig):
    """Configuración más los registros del workspace que `prepare` resuelve para una recogida."""

    records: WorkspaceRecords = Field(default_factory=WorkspaceRecords)
