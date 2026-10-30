"""Almacenamiento de la matriz: `controls/matrix.yaml` y revisiones de solo adición.

`matrix.yaml` no está en `SYNCED_KEYS`: un push no debe revocar el alcance ni la Declaración.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import yaml
from pydantic import BaseModel

from ens_evidence.approvals import load_model
from ens_evidence.matrix.model import ApplicabilityReview, MatrixFile, MeasureReviewRecord
from ens_evidence.workspace import NotFound, NotText, WorkspaceStore

MATRIX_KEY = "controls/matrix.yaml"
REVIEWS_PREFIX = "controls/reviews"

_MODELS: dict[str, type[BaseModel]] = {
    "implementation": MeasureReviewRecord,
    "applicability": ApplicabilityReview,
}

Record = MeasureReviewRecord | ApplicabilityReview


@dataclass(frozen=True)
class StoredReview[R: Record]:
    key: str
    record: R


@dataclass(frozen=True)
class EffectiveReviews:
    """Última revisión de cada eje con `recorded_at <= at`."""

    implementation: StoredReview[MeasureReviewRecord] | None
    applicability: StoredReview[ApplicabilityReview] | None
    errors: list[str]
    unreadable: list[str] = field(default_factory=list)


def _dump(model: BaseModel) -> str:
    return yaml.safe_dump(model.model_dump(mode="json"), sort_keys=False, allow_unicode=True)


def read_matrix(store: WorkspaceStore) -> tuple[MatrixFile | None, list[str]]:
    """(None, []) si aún no existe; (None, errores) si no se lee o contiene secretos."""
    if not store.exists(MATRIX_KEY):
        return None, []
    errors: list[str] = []
    matrix = load_model(store, MATRIX_KEY, MatrixFile, errors)
    return matrix, errors


def save_matrix(store: WorkspaceStore, matrix: MatrixFile) -> None:
    store.write_text(MATRIX_KEY, _dump(matrix))


def reviews_prefix(measure_id: str) -> str:
    return f"{REVIEWS_PREFIX}/{measure_id}"


def add_review(store: WorkspaceStore, record: Record) -> str:
    """Escribe la revisión como fichero nuevo y devuelve su clave; nunca sobrescribe."""
    stamp = record.recorded_at.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    key = f"{reviews_prefix(record.measure_id)}/{stamp}.yaml"
    store.create_bytes(key, _dump(record).encode("utf-8"))
    return key


def _axis(store: WorkspaceStore, key: str) -> str | None:
    try:
        raw: Any = yaml.safe_load(store.read_text(key))
    except (NotFound, NotText, yaml.YAMLError):
        return None
    return raw.get("axis") if isinstance(raw, dict) else None


@dataclass(frozen=True)
class ReviewScan:
    """`unreadable`: claves del historial que no se pueden leer. Su contenido es desconocido
    (podría ser una revisión posterior que revoca la anterior), así que bloquean la medida."""

    found: list[StoredReview]
    errors: list[str]
    unreadable: list[str]


def scan_reviews(store: WorkspaceStore, measure_id: str) -> ReviewScan:
    found: list[StoredReview] = []
    errors: list[str] = []
    unreadable: list[str] = []
    for key in store.list(reviews_prefix(measure_id)):
        before = len(errors)
        stored = _read_one(store, measure_id, key, errors)
        if stored is not None:
            found.append(stored)
        if len(errors) > before:
            unreadable.append(key)
    found.sort(key=lambda s: (s.record.recorded_at, s.key))
    return ReviewScan(found, errors, unreadable)


def _read_one(
    store: WorkspaceStore, measure_id: str, key: str, errors: list[str]
) -> StoredReview | None:
    model = _MODELS.get(_axis(store, key) or "") if key.endswith(".yaml") else None
    if model is None:
        errors.append(f"{key}: no es una revisión de la matriz reconocible")
        return None
    record = load_model(store, key, model, errors)
    if record is None:
        return None
    if record.measure_id != measure_id:
        errors.append(f"{key}: la revisión es de otra medida ({record.measure_id})")
        return None
    return StoredReview(key, record)


def read_reviews(store: WorkspaceStore, measure_id: str) -> tuple[list[StoredReview], list[str]]:
    """Todas las revisiones legibles de la medida, ordenadas por (recorded_at, clave)."""
    scan = scan_reviews(store, measure_id)
    return scan.found, scan.errors


def effective_reviews(store: WorkspaceStore, measure_id: str, at: datetime) -> EffectiveReviews:
    """La última revisión de cada eje registrada en o antes de `at` (las posteriores no existen)."""
    scan = scan_reviews(store, measure_id)
    visible = [s for s in scan.found if s.record.recorded_at <= at]
    implementation = [s for s in visible if isinstance(s.record, MeasureReviewRecord)]
    applicability = [s for s in visible if isinstance(s.record, ApplicabilityReview)]
    return EffectiveReviews(
        implementation[-1] if implementation else None,
        applicability[-1] if applicability else None,
        scan.errors,
        scan.unreadable,
    )
