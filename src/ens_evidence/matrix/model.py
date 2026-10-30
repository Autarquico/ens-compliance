"""Contratos de la matriz de cumplimiento (anexo III, 2.1.a del RD 311/2022).

La matriz registra, por medida, responsable, periodicidad y enlaces a fuentes; las revisiones
humanas son registros aparte, de solo adición. Dos ejes distintos (ADR 0002): la implantación
(`MeasureAssessment`) y la aplicabilidad (`Applicability`).
"""

import re
from collections import Counter
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, Field, model_validator

from ens_evidence.catalog.controls import CatalogVersion
from ens_evidence.evidence.layout import is_evidence_id
from ens_evidence.models import MeasureAssessment, Review, Role
from ens_evidence.models.contracts import SHA256_PATTERN, Strict
from ens_evidence.models.dossier import SELF_ASSESSORS

MEASURE_ID_PATTERN = r"^[a-z]+(\.[a-z0-9]+)+$"
COVERS_PATTERN = r"^(base|compensation|R[0-9]+|[a-z]+(\.[a-z0-9]+)+)$"
MAX_REVIEW_EVERY_DAYS = 365

MeasureId = Annotated[str, Field(pattern=MEASURE_ID_PATTERN)]
Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]


class Owner(Strict):
    name: str = Field(min_length=1)
    role: Role


class _Link(Strict):
    covers: list[str] = Field(
        min_length=1,
        description=(
            "Qué cubre la fuente: ids de cláusula o los atajos `base`, `Rn` y `compensation`. "
            "Es obligatorio y explícito."
        ),
    )

    @model_validator(mode="after")
    def _covers_valid(self) -> Self:
        invalid = [c for c in self.covers if not re.fullmatch(COVERS_PATTERN, c)]
        if invalid:
            raise ValueError(f"covers no válido: {', '.join(invalid)}")
        repeated = sorted(c for c, n in Counter(self.covers).items() if n > 1)
        if repeated:
            raise ValueError(f"covers repetido: {', '.join(repeated)}")
        return self


class ManualEvidenceLink(_Link):
    kind: Literal["evidence"] = "evidence"
    evidence_id: str

    @model_validator(mode="after")
    def _evidence_id_valid(self) -> Self:
        if not is_evidence_id(self.evidence_id):
            raise ValueError(f"id de evidencia no válido: {self.evidence_id}")
        return self

    @property
    def target(self) -> tuple[str, str]:
        return (self.kind, self.evidence_id)


class CheckLink(_Link):
    kind: Literal["check"] = "check"
    check_id: str = Field(min_length=1)

    @property
    def target(self) -> tuple[str, str]:
        return (self.kind, self.check_id)


class DocumentLink(_Link):
    kind: Literal["org_document"] = "org_document"
    document: str = Field(min_length=1)

    @property
    def target(self) -> tuple[str, str]:
        return (self.kind, self.document)


Link = Annotated[ManualEvidenceLink | CheckLink | DocumentLink, Field(discriminator="kind")]


class MatrixEntry(Strict):
    id: MeasureId
    owner: Owner | None = None
    review_every_days: int | None = Field(
        default=None,
        ge=1,
        le=MAX_REVIEW_EVERY_DAYS,
        description="Periodicidad de revisión en días; explícita, sin valor por defecto.",
    )
    links: list[Link] = Field(default_factory=list)
    notes: str | None = None

    @model_validator(mode="after")
    def _no_duplicate_links(self) -> Self:
        repeated = sorted(
            f"{kind}:{target}"
            for (kind, target), n in Counter(link.target for link in self.links).items()
            if n > 1
        )
        if repeated:
            raise ValueError(f"enlaces repetidos: {', '.join(repeated)}")
        return self


class MatrixFile(Strict):
    catalog_version: CatalogVersion
    entries: list[MatrixEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_ids(self) -> Self:
        repeated = sorted(i for i, n in Counter(e.id for e in self.entries).items() if n > 1)
        if repeated:
            raise ValueError(f"ids de medida repetidos: {', '.join(repeated)}")
        return self


class _ReviewRecord(Strict):
    measure_id: MeasureId
    review: Review
    recorded_at: AwareDatetime = Field(description="Cuándo se registró; la fecha del registro.")
    measure_context_sha256: Sha256

    @model_validator(mode="after")
    def _reviewer_and_dates(self) -> Self:
        if self.review.role not in SELF_ASSESSORS:
            raise ValueError(
                "solo registran revisiones el administrador del sistema "
                "(system_administrator) o su delegado (system_administrator_delegate)"
            )
        if self.review.reviewed_at > self.recorded_at:
            raise ValueError("reviewed_at no puede ser posterior a recorded_at")
        return self


class ReviewedEvidence(Strict):
    evidence_id: str
    artifact_sha256: Sha256


class ReviewedDocument(Strict):
    name: str = Field(min_length=1)
    document_sha256: Sha256


class ReviewedClause(Strict):
    """Cláusula que la revisión confirmó y las fuentes (`tipo:destino`) que la cubren."""

    clause: str = Field(min_length=1)
    sources: list[str] = Field(default_factory=list)


class ReviewedObligation(Strict):
    """Fuentes que la persona revisora vio para una obligación de la medida.

    `coverage` es la única lista de cláusulas confirmadas."""

    obligation: str = Field(min_length=1)
    coverage: list[ReviewedClause] = Field(
        default_factory=list, description="Qué fuentes cubrían cada cláusula al revisar."
    )
    evidence: list[ReviewedEvidence] = Field(default_factory=list)
    checks: list[str] = Field(default_factory=list)
    documents: list[ReviewedDocument] = Field(default_factory=list)

    @property
    def has_sources(self) -> bool:
        return bool(self.evidence or self.checks or self.documents)


class MeasureReviewRecord(_ReviewRecord):
    """Revisión del eje de implantación; nunca NOT_ASSESSED."""

    axis: Literal["implementation"] = "implementation"
    assessment: MeasureAssessment
    obligations: list[ReviewedObligation] = Field(default_factory=list)
    links_sha256: Sha256

    @model_validator(mode="after")
    def _assessment_and_sources(self) -> Self:
        if self.assessment is MeasureAssessment.NOT_ASSESSED:
            raise ValueError("una revisión no puede dejar la medida en NOT_ASSESSED")
        repeated = sorted(
            o for o, n in Counter(r.obligation for r in self.obligations).items() if n > 1
        )
        if repeated:
            raise ValueError(f"obligaciones repetidas: {', '.join(repeated)}")
        if self.assessment is MeasureAssessment.COMPLIANT and not any(
            o.has_sources for o in self.obligations
        ):
            raise ValueError("COMPLIANT exige las fuentes revisadas (evidencia, check o documento)")
        return self


class ApplicabilityReview(_ReviewRecord):
    """Revisión del eje de aplicabilidad (medida no aplicable); no lleva valoración."""

    axis: Literal["applicability"] = "applicability"
