"""Registro de cada documento generado (`org/docs/<doc>.meta.yaml`)."""

from datetime import date
from typing import Literal

from pydantic import Field

from ens_evidence.models.contracts import SHA256_PATTERN, Strict


class TemplateRef(Strict):
    id: str
    version: int


class SupportedClause(Strict):
    id: str
    origin: Literal["catalog", "articulado"]
    section: str


class SupportedMeasure(Strict):
    id: str
    covered_clauses: list[str]
    missing_clauses: list[str] = Field(
        description="Cláusulas del cuerpo de la medida que el documento no cubre."
    )


class DocumentMeta(Strict):
    """Cubrir una cláusula no acredita que la medida esté implantada."""

    document: str
    template: TemplateRef
    renderer_version: int
    catalog_sha256: str = Field(pattern=SHA256_PATTERN)
    catalog_consolidation_date: date
    articulado_sha256: str = Field(pattern=SHA256_PATTERN)
    articulado_consolidation_date: date
    answers_sha256: str = Field(
        pattern=SHA256_PATTERN, description="Sha256 de las respuestas que usa este documento."
    )
    document_sha256: str = Field(pattern=SHA256_PATTERN)
    clauses: list[SupportedClause]
    measures: list[SupportedMeasure]
