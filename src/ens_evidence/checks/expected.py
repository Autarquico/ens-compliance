"""Sujetos que cada check debe cubrir (D5), alineados con lo que producen los checks.

GitHub: la organización para `org_2fa_required`; los repositorios del inventario para el resto,
sin los archivados en los checks con `skip_archived`. El archivado y la grafía del nombre salen
de la evidencia recogida (la API manda sobre el inventario). Endpoint: laptops y servidores del
inventario (solo laptops en los checks de puesto). Los demás conectores no tienen sujetos de
inventario: la completitud la deciden sus propios checks.
"""

from typing import Any

from pydantic import BaseModel

from ens_evidence.checks.catalog import CHECKS, SubjectKind
from ens_evidence.connectors.github.config import GitHubConfig
from ens_evidence.connectors.github.connector import inventory_repo_names
from ens_evidence.endpoint.importer import ENDPOINT_ASSET_TYPES
from ens_evidence.models.contracts import CollectionResult
from ens_evidence.models.enums import CollectionStatus
from ens_evidence.scope import AssetType, Inventory
from ens_evidence.workspace import WorkspaceError

INVENTORY_ASSET_INVALID = "inventory_asset_invalid"


class ExpectedError(Exception):
    """El inventario no permite saber qué sujetos se esperan; `code` es estable."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def expected_subjects(
    connector: str,
    config: BaseModel,
    inventory: Inventory,
    evidence: CollectionResult | None,
) -> dict[str, list[str]]:
    """Por id de check, los sujetos esperados, ordenados. `evidence` es la última recogida del
    conector (None si no hay una utilizable): sin ella no se conocen los archivados."""
    expected: dict[str, list[str]] = {}
    for check_id, spec in CHECKS.items():
        if spec.connector != connector:
            continue
        match spec.subjects:
            case SubjectKind.ORGANIZATION:
                expected[check_id] = [_github(config).organization]
            case SubjectKind.REPOSITORY:
                repos = _repositories(_github(config), inventory, evidence, spec.skip_archived)
                expected[check_id] = repos
            case SubjectKind.HOST:
                expected[check_id] = sorted(
                    a.id for a in inventory.assets.assets if a.type in ENDPOINT_ASSET_TYPES
                )
            case SubjectKind.WORKSTATION:
                expected[check_id] = sorted(
                    a.id for a in inventory.assets.assets if a.type is AssetType.LAPTOP
                )
            case SubjectKind.SELF_REPORTED:
                expected[check_id] = []
    return expected


def _github(config: BaseModel) -> GitHubConfig:
    assert isinstance(config, GitHubConfig)
    return config


def _repositories(
    config: GitHubConfig,
    inventory: Inventory,
    evidence: CollectionResult | None,
    skip_archived: bool,
) -> list[str]:
    asset = next((a for a in inventory.assets.assets if a.id == config.asset_id), None)
    if asset is None or asset.type is not AssetType.CODE_HOSTING:
        raise ExpectedError(INVENTORY_ASSET_INVALID)
    try:
        names = inventory_repo_names(asset.repos or [], config.organization)
    except WorkspaceError:
        raise ExpectedError(INVENTORY_ASSET_INVALID) from None
    seen = _collected_repositories(evidence)
    chosen: dict[str, str] = {}
    for name in names:
        item = seen.get(name.casefold())
        if skip_archived and item is not None and item.get("archived") is True:
            continue
        spelling = item.get("name") if item is not None else None
        chosen.setdefault(name.casefold(), spelling if isinstance(spelling, str) else name)
    return sorted(chosen.values())


def _collected_repositories(evidence: CollectionResult | None) -> dict[str, dict[str, Any]]:
    """Repositorios de la evidencia por nombre en minúsculas; vacío si la sección no se recogió."""
    if evidence is None:
        return {}
    sections = evidence.data.get("sections")
    section = sections.get("repositories") if isinstance(sections, dict) else None
    if not isinstance(section, dict) or section.get("status") != CollectionStatus.SUCCESS.value:
        return {}
    items = section.get("items")
    if not isinstance(items, list):
        return {}
    return {
        item["name"].casefold(): item
        for item in items
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
