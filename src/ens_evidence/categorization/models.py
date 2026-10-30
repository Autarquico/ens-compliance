"""Modelo de `scope/categorization.yaml`: valoraciones por información y servicio (anexo I, 3)."""

from typing import Literal, Self

from pydantic import AwareDatetime, Field, field_validator, model_validator

from ens_evidence.catalog.model import Dimension, SecurityLevel
from ens_evidence.models.contracts import Strict
from ens_evidence.models.enums import Role

NOT_AFFECTED = "not_affected"


class Rating(Strict):
    """Nivel de una dimensión, o `not_affected`, con su justificación."""

    level: SecurityLevel | Literal["not_affected"]
    justification: str = Field(
        min_length=1,
        description="Razón del nivel, referida a los criterios de perjuicio del anexo I, 3.",
    )

    @field_validator("justification")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError(
                "la justificación no puede estar vacía; cita el criterio del anexo I, 3"
            )
        return value

    @property
    def security_level(self) -> SecurityLevel | None:
        return None if self.level == NOT_AFFECTED else SecurityLevel(self.level)


class _Valuation(Strict):
    valued_by: str = Field(min_length=1)
    role: Role
    valued_at: AwareDatetime

    @field_validator("valued_by")
    @classmethod
    def _declared(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("quien valora no puede estar vacío")
        return value


class InformationValuation(_Valuation):
    @field_validator("role")
    @classmethod
    def _information_owner(cls, value: Role) -> Role:
        if value is not Role.INFORMATION_OWNER:
            raise ValueError("una información la valora su responsable (information_owner)")
        return value


class ServiceValuation(_Valuation):
    @field_validator("role")
    @classmethod
    def _service_owner(cls, value: Role) -> Role:
        if value is not Role.SERVICE_OWNER:
            raise ValueError("un servicio lo valora su responsable (service_owner)")
        return value


class _Element(Strict):
    id: str = Field(min_length=1)
    ratings: dict[Dimension, Rating] = Field(
        default_factory=dict, description="Una entrada por dimensión; ausente = pendiente."
    )

    @property
    def missing_dimensions(self) -> list[Dimension]:
        return [dimension for dimension in Dimension if dimension not in self.ratings]


class Information(_Element):
    description: str = Field(min_length=1)
    valuation: InformationValuation | None = None


class ServiceRating(_Element):
    valuation: ServiceValuation | None = None


class CategorizationFile(Strict):
    information: list[Information] = Field(default_factory=list)
    services: list[ServiceRating] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_ids(self) -> Self:
        for kind, elements in (("información", self.information), ("servicio", self.services)):
            ids = [element.id for element in elements]
            repeated = sorted({id_ for id_ in ids if ids.count(id_) > 1})
            if repeated:
                raise ValueError(f"{kind} con id repetido: {', '.join(repeated)}")
        return self
