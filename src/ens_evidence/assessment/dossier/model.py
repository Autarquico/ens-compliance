"""Instantánea del expediente: 11 secciones de la guía, cada una con sus fuentes.

Solo describe lo que se registró. Ningún estado de sección dice «conforme»: la
conformidad es un acto humano de la organización (regla 12).
"""

from enum import StrEnum
from typing import Self

from pydantic import AwareDatetime, Field, JsonValue, field_validator, model_validator

from ens_evidence.catalog.controls import CatalogVersion
from ens_evidence.matrix import MatrixExport
from ens_evidence.models.contracts import SHA256_PATTERN, Strict
from ens_evidence.models.enums import Category, Readiness

SECTION_TITLES = (
    "Identificación",
    "Alcance",
    "Categorización",
    "Riesgos",
    "Declaración de aplicabilidad",
    "Metodología",
    "Resultado por medida",
    "Evidencias",
    "Incumplimientos/tratamiento",
    "Conclusiones",
    "Aprobación/fecha",
)
RESULTS_SECTION = 7  # «Resultado por medida»: el único número de sección que se cita
COMMITMENT = "La herramienta no declara conformidad."
INTERNAL_REVIEW_NOTICE = (
    "Revisión interna; no sustituye a la auditoría de certificación (art. 38.1)."
)


class SectionState(StrEnum):
    RECORDED = "RECORDED"
    INCOMPLETE = "INCOMPLETE"
    PENDING_HUMAN_REVIEW = "PENDING_HUMAN_REVIEW"


class SourceRef(Strict):
    kind: str = Field(min_length=1)
    key: str = Field(min_length=1)
    sha256: str = Field(pattern=SHA256_PATTERN)
    timestamps: list[AwareDatetime] = Field(min_length=1)


class Section(Strict):
    number: int = Field(ge=1, le=len(SECTION_TITLES))
    title: str
    state: SectionState
    sources: list[SourceRef]
    content: JsonValue

    @model_validator(mode="after")
    def _title_matches_number(self) -> Self:
        if self.title != SECTION_TITLES[self.number - 1]:
            raise ValueError(
                f"section {self.number} must be titled {SECTION_TITLES[self.number - 1]!r}"
            )
        return self


class SnapshotInputs(Strict):
    """Entradas congeladas: con ellas la instantánea se reproduce byte a byte."""

    generated_at: AwareDatetime
    evaluated_at: AwareDatetime
    reference_now: AwareDatetime
    methodology_version: str = Field(min_length=1)
    product_version: str = Field(min_length=1)
    catalog_version: CatalogVersion
    check_runs: list[str] | None = Field(
        default=None,
        description="Claves de las ejecuciones de checks que existían al generar; se fijan al "
        "generar y la reproducción ve solo esas.",
    )
    preaudit_runs: list[str] | None = Field(
        default=None,
        description="Claves de las ejecuciones de pre-auditoría que existían al generar; la "
        "reproducción ve solo esas.",
    )
    preaudit_dispositions: list[str] | None = Field(
        default=None,
        description="Claves de las disposiciones de hallazgos que existían al generar; la "
        "reproducción ve solo esas.",
    )


class DossierSnapshot(Strict):
    category: Category
    commitment: str = COMMITMENT
    notice: str | None = None
    inputs: SnapshotInputs
    sections: list[Section]

    @field_validator("commitment")
    @classmethod
    def _fixed_commitment(cls, value: str) -> str:
        if value != COMMITMENT:
            raise ValueError("commitment is fixed text")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if [s.number for s in self.sections] != list(range(1, len(SECTION_TITLES) + 1)):
            raise ValueError("exactly 11 sections numbered 1 to 11, in order")
        expected = None if self.category is Category.BASICA else INTERNAL_REVIEW_NOTICE
        if self.notice != expected:
            raise ValueError(f"notice for {self.category} must be {expected!r}")
        return self

    def section(self, number: int) -> Section:
        """La sección por su número de la guía (1 a 11), nunca por índice."""
        return self.sections[number - 1]


def snapshot_export(snapshot: DossierSnapshot) -> MatrixExport:
    """La matriz incrustada en «Resultado por medida», tipada."""
    content = snapshot.section(RESULTS_SECTION).content
    if not isinstance(content, dict):
        raise ValueError("la sección de resultados no tiene el formato esperado")
    return MatrixExport.model_validate(content["matrix"])


def snapshot_readiness(snapshot: DossierSnapshot) -> Readiness | None:
    """La preparación incrustada en la instantánea; None si la sección 7 no se puede leer."""
    try:
        return snapshot_export(snapshot).readiness
    except (ValueError, KeyError):
        return None
