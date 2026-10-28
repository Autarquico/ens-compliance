"""Piezas comunes de las secciones normalizadas de cualquier conector."""

from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ens_evidence.models.enums import CollectionStatus

_SEVERITY = (
    CollectionStatus.ERROR,
    CollectionStatus.PERMISSION_DENIED,
    CollectionStatus.NOT_SUPPORTED,
    CollectionStatus.SUCCESS,
)


class Section(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: CollectionStatus
    reason: str | None = None
    items: list[dict[str, Any]] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)


def worst(statuses: Iterable[CollectionStatus]) -> CollectionStatus:
    present = set(statuses)
    return next((status for status in _SEVERITY if status in present), CollectionStatus.SUCCESS)
