"""Articulado y anexo III del RD 311/2022 empaquetados, con revisión humana ligada a su hash."""

import hashlib
from datetime import date
from functools import cache
from importlib import resources

import yaml
from pydantic import AwareDatetime, Field, ValidationError

from ens_evidence.catalog.review import ReviewState, ReviewStatus
from ens_evidence.models.contracts import SHA256_PATTERN, Strict

SOURCES = resources.files("ens_evidence.orgdocs") / "sources"
DATA_FILE = "articulado.yaml"
REVIEW_FILE = "review.yaml"


class SourceClause(Strict):
    id: str
    reference: str
    text: str


class Articulado(Strict):
    regulation: str
    url: str
    consolidation_date: date
    snapshot: str
    snapshot_sha256: str = Field(pattern=SHA256_PATTERN)
    clauses: list[SourceClause]


class SourceReview(Strict):
    articulado_sha256: str = Field(pattern=SHA256_PATTERN)
    reviewed_by: str = Field(min_length=1)
    reviewed_at: AwareDatetime


@cache
def load_articulado() -> Articulado:
    articulado = Articulado.model_validate(
        yaml.safe_load((SOURCES / DATA_FILE).read_text(encoding="utf-8"))
    )
    snapshot = (SOURCES / articulado.snapshot).read_bytes()
    if hashlib.sha256(snapshot).hexdigest() != articulado.snapshot_sha256:
        raise ValueError("articulado.html no coincide con el sha256 de articulado.yaml")
    return articulado


@cache
def articulado_sha256() -> str:
    return hashlib.sha256((SOURCES / DATA_FILE).read_bytes()).hexdigest()


def review_status() -> ReviewStatus:
    file = SOURCES / REVIEW_FILE
    if not file.is_file():
        return ReviewStatus(ReviewState.UNREVIEWED, "sin revisar: falta sources/review.yaml")
    try:
        review = SourceReview.model_validate(yaml.safe_load(file.read_text(encoding="utf-8")))
    except (yaml.YAMLError, ValidationError, UnicodeDecodeError):
        return ReviewStatus(ReviewState.INVALID, "sources/review.yaml no es válido")
    if review.articulado_sha256 != articulado_sha256():
        return ReviewStatus(
            ReviewState.STALE,
            "el articulado cambió después de revisarse; hace falta una revisión nueva",
            review,
        )
    return ReviewStatus(
        ReviewState.CURRENT,
        f"revisado por {review.reviewed_by} el {review.reviewed_at.isoformat()}",
        review,
    )
