"""Contexto vigente de la aplicabilidad: alcance aprobado y categorización vigente ahora."""

from dataclasses import dataclass
from datetime import datetime

from ens_evidence.applicability.selection import Selection, basis_of, select
from ens_evidence.catalog import load_catalog
from ens_evidence.catalog.controls import CatalogVersion, current_catalog_version
from ens_evidence.catalog.model import Catalog
from ens_evidence.categorization import CategorizationStatus, require_current_categorization
from ens_evidence.models.enums import Category
from ens_evidence.models.readiness import SelectionBasis
from ens_evidence.scope import ScopeStatus, require_approved_scope
from ens_evidence.workspace import WorkspaceStore


@dataclass(frozen=True)
class Context:
    scope: ScopeStatus
    categorization: CategorizationStatus
    category: Category
    catalog: Catalog
    version: CatalogVersion
    selections: dict[str, Selection]

    def basis(self, measure_id: str) -> SelectionBasis:
        return basis_of(self.selections[measure_id], self.category, self.version.snapshot_sha256)


def load_context(store: WorkspaceStore, now: datetime) -> Context:
    """Lanza `ScopeNotApproved` o `CategorizationNotCurrent` si falta el contexto."""
    scope = require_approved_scope(store)
    categorization = require_current_categorization(store, now)
    assert categorization.computation is not None
    category = categorization.computation.category
    assert category is not None
    catalog = load_catalog()
    return Context(
        scope,
        categorization,
        category,
        catalog,
        current_catalog_version(catalog),
        {m.id: select(m, categorization.computation) for m in catalog.measures},
    )
