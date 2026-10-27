"""Revisión humana del catálogo, ligada a su hash (`review.yaml`)."""

from dataclasses import dataclass
from enum import StrEnum
from importlib import resources

import yaml
from pydantic import AwareDatetime, Field, ValidationError

from ens_evidence.catalog.load import catalog_sha256
from ens_evidence.models.contracts import SHA256_PATTERN, Strict

REVIEW_FILE = "review.yaml"


class CatalogReview(Strict):
    catalog_sha256: str = Field(pattern=SHA256_PATTERN)
    reviewed_by: str = Field(min_length=1)
    reviewed_at: AwareDatetime


class ReviewState(StrEnum):
    UNREVIEWED = "UNREVIEWED"
    CURRENT = "CURRENT"
    STALE = "STALE"
    INVALID = "INVALID"


@dataclass(frozen=True)
class ReviewStatus:
    state: ReviewState
    message: str
    review: CatalogReview | None = None


def review_status() -> ReviewStatus:
    file = resources.files("ens_evidence.catalog") / REVIEW_FILE
    if not file.is_file():
        return ReviewStatus(ReviewState.UNREVIEWED, "sin revisar: falta review.yaml")
    try:
        review = CatalogReview.model_validate(yaml.safe_load(file.read_text(encoding="utf-8")))
    except (yaml.YAMLError, ValidationError, UnicodeDecodeError):
        return ReviewStatus(ReviewState.INVALID, "review.yaml no es válido")
    if review.catalog_sha256 != catalog_sha256():
        return ReviewStatus(
            ReviewState.STALE,
            "el catálogo cambió después de revisarse; hace falta una revisión nueva",
            review,
        )
    return ReviewStatus(
        ReviewState.CURRENT,
        f"revisado por {review.reviewed_by} el {review.reviewed_at.isoformat()}",
        review,
    )
