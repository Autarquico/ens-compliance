"""Ata el expediente a la categorización aprobada vigente (ADR 0009)."""

from dataclasses import dataclass
from datetime import datetime

from ens_evidence.categorization.approval import require_current_categorization
from ens_evidence.categorization.calculation import commitment, commitment_notice
from ens_evidence.models.dossier import Dossier
from ens_evidence.models.enums import Category
from ens_evidence.workspace import WorkspaceStore


class DossierCategoryMismatch(ValueError):
    def __init__(self, dossier: Category, approved: Category) -> None:
        super().__init__(
            f"el expediente es {dossier.value} y la categoría aprobada vigente es {approved.value}"
        )


@dataclass(frozen=True)
class OpenedDossier:
    dossier: Dossier
    commitment: str
    notice: str


def open_dossier(store: WorkspaceStore, now: datetime) -> OpenedDossier:
    """Expediente nuevo con la categoría aprobada vigente, o `CategorizationNotCurrent`."""
    category = _approved_category(store, now)
    return OpenedDossier(
        Dossier(category=category), commitment(category), commitment_notice(category)
    )


def require_dossier_category(dossier: Dossier, store: WorkspaceStore, now: datetime) -> str:
    """Compromiso del expediente, o `DossierCategoryMismatch` si no es la categoría aprobada."""
    approved = _approved_category(store, now)
    if dossier.category is not approved:
        raise DossierCategoryMismatch(dossier.category, approved)
    return commitment(approved)


def _approved_category(store: WorkspaceStore, now: datetime) -> Category:
    approval = require_current_categorization(store, now).approval
    assert approval is not None  # CURRENT siempre lleva aprobación
    return approval.category
