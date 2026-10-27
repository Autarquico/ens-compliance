"""Estado de las medidas de una organización: `controls/measures.yaml` (ADR 0007)."""

from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import Any, Self

import yaml
from pydantic import Field, ValidationError, model_validator

from ens_evidence.catalog.load import load_catalog
from ens_evidence.catalog.model import Catalog
from ens_evidence.models.contracts import SHA256_PATTERN, Strict
from ens_evidence.models.enums import Applicability, MeasureAssessment
from ens_evidence.models.readiness import Measure
from ens_evidence.scope.messages import describe_validation
from ens_evidence.scope.sync import MEASURES_KEY
from ens_evidence.workspace import NotFound, NotText, WorkspaceStore


class CatalogVersion(Strict):
    snapshot_sha256: str = Field(pattern=SHA256_PATTERN)
    consolidation_date: date


class MeasuresFile(Strict):
    catalog_version: CatalogVersion
    measures: list[Measure]

    @model_validator(mode="after")
    def _unique_ids(self) -> Self:
        repeated = sorted(i for i, n in Counter(m.id for m in self.measures).items() if n > 1)
        if repeated:
            raise ValueError(f"ids de medida repetidos: {', '.join(repeated)}")
        return self


class MeasuresFileError(Exception):
    """El fichero no se pudo leer o validar, o contiene IDs ajenos al catálogo."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass(frozen=True)
class InitResult:
    created: list[str]
    kept: int
    previous_version: CatalogVersion | None
    version: CatalogVersion

    @property
    def replaced_version(self) -> CatalogVersion | None:
        """La versión anterior si el catálogo cambió desde el último `init`."""
        return self.previous_version if self.previous_version != self.version else None


def current_catalog_version(catalog: Catalog) -> CatalogVersion:
    source = catalog.measures[0].source
    return CatalogVersion(
        snapshot_sha256=source.snapshot_sha256, consolidation_date=source.consolidation_date
    )


def initial_measure(measure_id: str) -> Measure:
    return Measure(
        id=measure_id,
        applicability=Applicability.UNDETERMINED,
        assessment=MeasureAssessment.NOT_ASSESSED,
    )


def load_measures(store: WorkspaceStore) -> MeasuresFile | None:
    """None si el fichero no existe; MeasuresFileError si no es legible o válido."""
    try:
        text = store.read_text(MEASURES_KEY)
    except NotFound:
        return None
    except NotText:
        raise MeasuresFileError(
            [f"{MEASURES_KEY}: no es texto UTF-8; guárdalo como UTF-8"]
        ) from None
    try:
        raw: Any = yaml.safe_load(text)
    except yaml.YAMLError:
        raise MeasuresFileError(
            [f"{MEASURES_KEY}: no es YAML válido; revisa la sintaxis"]
        ) from None
    try:
        return MeasuresFile.model_validate(raw)
    except ValidationError as exc:
        raise MeasuresFileError(describe_validation(MEASURES_KEY, exc, raw)) from None


def init_measures(store: WorkspaceStore) -> InitResult:
    """Crea las medidas que faltan, conserva las existentes y fija la versión del catálogo."""
    catalog = load_catalog()
    version = current_catalog_version(catalog)
    existing = load_measures(store)
    kept = {m.id: m for m in existing.measures} if existing else {}
    known = {m.id for m in catalog.measures}
    foreign = sorted(set(kept) - known)
    if foreign:
        raise MeasuresFileError(
            [
                f"{MEASURES_KEY} › {i}: no está en el catálogo; bórrala o corrige el id"
                for i in foreign
            ]
        )
    created = [m.id for m in catalog.measures if m.id not in kept]
    measures = [kept.get(m.id) or initial_measure(m.id) for m in catalog.measures]
    result = InitResult(
        created=created,
        kept=len(kept),
        previous_version=existing.catalog_version if existing else None,
        version=version,
    )
    if existing is None or created or result.replaced_version:
        document = MeasuresFile(catalog_version=version, measures=measures)
        text = yaml.safe_dump(
            document.model_dump(mode="json", exclude_none=True),
            sort_keys=False,
            allow_unicode=True,
        )
        store.write_text(MEASURES_KEY, text)
    return result
