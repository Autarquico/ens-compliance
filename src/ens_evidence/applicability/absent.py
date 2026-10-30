"""Exigidas por el anexo II que no aplican porque falta el tipo de activo (anexo II, 2.1.a).

La lista de medidas y tipos de activo es decisión del producto (`absent_asset_types.yaml`).
"""

from dataclasses import dataclass
from functools import cache
from importlib import resources

import yaml

from ens_evidence.models.readiness import Measure
from ens_evidence.scope.models import Asset, AssetType, Deployment

_LIST = resources.files("ens_evidence.applicability") / "absent_asset_types.yaml"


@cache
def absent_asset_types() -> dict[str, tuple[str, ...]]:
    raw = yaml.safe_load(_LIST.read_text(encoding="utf-8"))
    return {measure_id: tuple(types) for measure_id, types in raw.items()}


@dataclass(frozen=True)
class Exclusion:
    id: str
    name: str
    asset_type: str
    justification: str


def _contradicting_asset(measure_id: str, assets: list[Asset]) -> str | None:
    """El tipo de activo del inventario que desmiente la exclusión, solo si es inequívoco."""
    deployed = [a for a in assets if a.deployment is Deployment.DEPLOYED]
    if measure_id == "mp.eq.3":
        found = any(a.type is AssetType.LAPTOP for a in deployed)
        return "un portátil (laptop)" if found else None
    if measure_id == "mp.s.2":
        found = any(a.type is AssetType.APPLICATION and a.domain for a in deployed)
        return "una aplicación con dominio" if found else None
    if measure_id == "mp.sw.1":
        if any(a.type is AssetType.APPLICATION and a.repo for a in deployed):
            return "una aplicación con repositorio"
        if any(a.type is AssetType.CODE_HOSTING for a in deployed):
            return "un alojamiento de código"
        return None
    if measure_id.startswith("op.ext."):
        found = any(a.type is AssetType.PROVIDER for a in deployed)
        return "un proveedor" if found else None
    return None


def absent_asset_warnings(measures: list[Measure], assets: list[Asset]) -> list[str]:
    """Señales del inventario que no desmienten la ausencia pero que conviene confirmar.

    Un aviso por servidor desplegado, con todas las mp.if.* excluidas por ausencia.
    """
    excluded = [m.id for m in measures if m.absent_assets is not None and m.id.startswith("mp.if.")]
    if not excluded:
        return []
    return [
        f"hay un servidor desplegado ({a.id}, {a.location or 'sin ubicación'}); "
        f"confirma que no está en instalaciones propias antes de excluir {', '.join(excluded)}"
        for a in assets
        if a.type is AssetType.SERVER and a.deployment is Deployment.DEPLOYED
    ]


def absent_asset_errors(measure: Measure, where: str, assets: list[Asset]) -> list[str]:
    absent = measure.absent_assets
    if absent is None:
        return []
    allowed = absent_asset_types().get(measure.id)
    if allowed is None:
        return [
            f"{where}: no se refiere a un tipo de activo que pueda faltar; "
            "aplícala o compénsala (art. 28.3)"
        ]
    errors = []
    if absent.asset_type not in allowed:
        errors.append(f"{where}: absent_assets.asset_type debe ser uno de: {', '.join(allowed)}")
    if contradiction := _contradicting_asset(measure.id, assets):
        errors.append(
            f"{where}: el inventario dice que ese tipo de activo existe (hay {contradiction}); "
            "aplícala o compénsala (art. 28.3)"
        )
    return errors
