"""Modelos de `risk/`: metodología declarada, registro, plan de tratamiento y aprobación."""

import re
from collections import Counter
from datetime import date
from enum import StrEnum
from typing import Self

from pydantic import AwareDatetime, Field, field_validator, model_validator

from ens_evidence.approvals import SecurityOfficer, one_year_after
from ens_evidence.catalog.controls import CatalogVersion
from ens_evidence.catalog.model import Dimension, SecurityLevel
from ens_evidence.models.contracts import SHA256_PATTERN, Strict
from ens_evidence.models.enums import Role
from ens_evidence.scope.sync import (  # noqa: F401  (los nombres se reexportan desde aquí)
    METHODOLOGY_KEY,
    METHODOLOGY_MD_KEY,
    REGISTER_KEY,
    RISK_FILES,
    TREATMENT_PLAN_KEY,
)

APPROVAL_KEY = "risk/approval.yaml"


def _not_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("no puede estar vacío; rellénalo")
    return value


class ScaleValue(Strict):
    id: str = Field(min_length=1)
    description: str

    _described = field_validator("description")(_not_blank)


def _unique(values: list[ScaleValue], name: str) -> None:
    if len(values) < 2:
        raise ValueError(f"{name} necesita al menos dos valores")
    if any(n > 1 for n in Counter(v.id for v in values).values()):
        raise ValueError(f"{name} tiene ids repetidos")


class Methodology(Strict):
    adopted: bool
    name: str
    reference: str
    adaptation: str
    likelihood: list[ScaleValue]
    impact: list[ScaleValue]
    risk_levels: list[ScaleValue]
    matrix: dict[str, dict[str, str]]

    _named = field_validator("name", "reference", "adaptation")(_not_blank)

    @model_validator(mode="after")
    def _coherent(self) -> Self:
        for name in ("likelihood", "impact", "risk_levels"):
            _unique(getattr(self, name), name)
        levels = set(self.risk_level_ids)
        if set(self.matrix) != set(self.likelihood_ids):
            raise ValueError("matrix debe tener una fila por cada valor de likelihood")
        for row in self.matrix.values():
            if set(row) != set(self.impact_ids):
                raise ValueError("cada fila de matrix debe tener una columna por cada impact")
            if not set(row.values()) <= levels:
                raise ValueError("matrix solo puede usar valores de risk_levels")
        return self

    @property
    def likelihood_ids(self) -> list[str]:
        return [v.id for v in self.likelihood]

    @property
    def impact_ids(self) -> list[str]:
        return [v.id for v in self.impact]

    @property
    def risk_level_ids(self) -> list[str]:
        return [v.id for v in self.risk_levels]

    @property
    def minimum_level(self) -> str:
        return self.risk_levels[0].id

    def inherent(self, likelihood: str, impact: str) -> str | None:
        return self.matrix.get(likelihood, {}).get(impact)


class Treatment(StrEnum):
    MITIGATE = "mitigate"
    ACCEPT = "accept"
    TRANSFER = "transfer"
    AVOID = "avoid"


class Acceptance(Strict):
    accepted_by: str
    role: Role
    accepted_at: AwareDatetime

    _named = field_validator("accepted_by")(_not_blank)


class Risk(Strict):
    id: str = Field(min_length=1)
    assets: list[str] = Field(default_factory=list)
    services: list[str] = Field(default_factory=list)
    threat: str
    dimensions: list[Dimension] = Field(min_length=1)
    valuation: dict[Dimension, SecurityLevel]
    likelihood: str = Field(min_length=1)
    impact: str = Field(min_length=1)
    inherent: str = Field(min_length=1)
    safeguards: list[str] = Field(default_factory=list)
    residual: str = Field(min_length=1)
    treatment: Treatment
    owner: str
    acceptance: Acceptance | None = None

    _described = field_validator("threat", "owner")(_not_blank)

    @model_validator(mode="after")
    def _coherent(self) -> Self:
        if not self.assets and not self.services:
            raise ValueError("assets y services: indica al menos un activo o un servicio")
        if len(set(self.dimensions)) != len(self.dimensions):
            raise ValueError("dimensions: hay dimensiones repetidas")
        if set(self.valuation) != set(self.dimensions):
            raise ValueError("valuation: debe valorar exactamente las dimensiones afectadas")
        if self.treatment is Treatment.MITIGATE and not self.safeguards:
            raise ValueError("safeguards: mitigar exige al menos una medida")
        if self.treatment is Treatment.ACCEPT and self.acceptance is None:
            raise ValueError("acceptance: aceptar el riesgo exige registrar la aceptación")
        return self


class RegisterFile(Strict):
    risks: list[Risk]

    @model_validator(mode="after")
    def _unique_ids(self) -> Self:
        if any(n > 1 for n in Counter(r.id for r in self.risks).values()):
            raise ValueError("risks: hay ids de riesgo repetidos; cada id debe ser único")
        return self


class ActionStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    CANCELLED = "cancelled"


class TreatmentAction(Strict):
    id: str = Field(min_length=1)
    risk: str = Field(min_length=1)
    description: str
    owner: str
    due: date
    status: ActionStatus

    _described = field_validator("description", "owner")(_not_blank)


class TreatmentPlanFile(Strict):
    actions: list[TreatmentAction]

    @model_validator(mode="after")
    def _unique_ids(self) -> Self:
        if any(n > 1 for n in Counter(a.id for a in self.actions).values()):
            raise ValueError("actions: hay ids de acción repetidos; cada id debe ser único")
        return self


class RiskApproval(Strict):
    approved_by: str
    role: SecurityOfficer
    approved_at: AwareDatetime
    valid_until: AwareDatetime
    scope_fingerprint: str = Field(pattern=SHA256_PATTERN)
    catalog_version: CatalogVersion
    categorization_sha256: str | None = Field(
        default=None,
        pattern=SHA256_PATTERN,
        description="Categorización vigente al aprobar; un registro sin ella queda obsoleto.",
    )
    files: dict[str, str]

    _named = field_validator("approved_by")(_not_blank)

    @model_validator(mode="after")
    def _annual(self) -> Self:
        if self.valid_until != one_year_after(self.approved_at):
            raise ValueError("valid_until debe ser un año después de approved_at")
        return self

    @model_validator(mode="after")
    def _hashes(self) -> Self:
        if set(self.files) != set(RISK_FILES) or not all(
            re.fullmatch(SHA256_PATTERN, h) for h in self.files.values()
        ):
            raise ValueError("files: debe llevar el sha256 de cada fichero de risk/")
        return self
