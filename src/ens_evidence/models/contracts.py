"""Contratos de collector, check y manifiesto de evidencias."""

from collections.abc import Iterable
from enum import StrEnum
from typing import Any, NamedTuple

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from ens_evidence.models.enums import CheckStatus, CollectionStatus, Role

SHA256_PATTERN = r"^[0-9a-f]{64}$"


class Translation(NamedTuple):
    status: CheckStatus
    reason_code: str | None


def translate_collection(
    collection_status: CollectionStatus,
    *,
    data_present: bool = True,
    compliant: bool | None = None,
) -> Translation:
    """Traducción recogida -> check del ADR 0002."""
    match collection_status:
        case CollectionStatus.SUCCESS:
            if not data_present or compliant is None:
                return Translation(CheckStatus.UNKNOWN, None)
            return Translation(CheckStatus.PASS if compliant else CheckStatus.FAIL, None)
        case CollectionStatus.ERROR:
            return Translation(CheckStatus.ERROR, None)
        case CollectionStatus.PERMISSION_DENIED:
            return Translation(CheckStatus.UNKNOWN, "permission_denied")
        case CollectionStatus.NOT_SUPPORTED:
            return Translation(CheckStatus.UNKNOWN, "not_supported")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CollectionResult(Strict):
    schema_version: str = "1.0"
    source: str
    collector: str
    collected_at: AwareDatetime
    asset_id: str
    data: dict[str, Any] = Field(default_factory=dict)
    collection_status: CollectionStatus


class CheckResult(Strict):
    check_id: str
    asset_id: str
    status: CheckStatus
    evaluated_at: AwareDatetime
    evidence_ids: list[str] = Field(default_factory=list)
    reason: str
    subject: str | None = None
    collection_status: CollectionStatus = CollectionStatus.SUCCESS
    reason_code: str | None = None

    @model_validator(mode="after")
    def _consistent_with_collection(self) -> "CheckResult":
        if self.status is CheckStatus.PASS and not self.evidence_ids:
            raise ValueError("PASS requires at least one evidence_id")
        if self.collection_status is not CollectionStatus.SUCCESS:
            expected = translate_collection(self.collection_status)
            if self.status is not expected.status or self.reason_code != expected.reason_code:
                raise ValueError(
                    f"collection {self.collection_status} implies check "
                    f"{expected.status} (reason_code={expected.reason_code!r})"
                )
        return self


class Review(Strict):
    reviewer: str
    role: Role
    reviewed_at: AwareDatetime
    valid_until: AwareDatetime
    conclusion: str
    evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validity_after_review(self) -> "Review":
        if self.valid_until <= self.reviewed_at:
            raise ValueError("valid_until must be after reviewed_at")
        return self


class Artifact(Strict):
    storage: str
    key: str
    sha256: str = Field(pattern=SHA256_PATTERN)


class CollectorInfo(Strict):
    name: str
    version: str


class ProvenanceKind(StrEnum):
    COLLECTOR = "collector"
    MANUAL = "manual"


class Provenance(Strict):
    kind: ProvenanceKind
    submitted_by: str = Field(min_length=1)
    submitted_at: AwareDatetime


class EvidenceManifest(Strict):
    id: str
    source: str
    asset_id: str
    collected_at: AwareDatetime
    valid_until: AwareDatetime | None = None
    artifact: Artifact
    provenance: Provenance
    collector: CollectorInfo | None = None
    review: Review | None = None

    @model_validator(mode="after")
    def _consistent(self) -> "EvidenceManifest":
        if self.provenance.kind is ProvenanceKind.COLLECTOR and self.collector is None:
            raise ValueError("una evidencia de collector necesita el campo collector")
        if self.valid_until is not None and self.valid_until <= self.collected_at:
            raise ValueError("valid_until debe ser posterior a collected_at")
        return self


def unresolved_evidence_ids(check: CheckResult, manifests: Iterable[EvidenceManifest]) -> list[str]:
    """Evidence ids citados por el check que no existen en los manifiestos."""
    known = {manifest.id for manifest in manifests}
    return [evidence_id for evidence_id in check.evidence_ids if evidence_id not in known]
