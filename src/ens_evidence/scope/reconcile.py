"""Reconciliación informativa entre el inventario y los hosts observados."""

from collections import Counter
from enum import StrEnum

import yaml
from pydantic import AwareDatetime, BaseModel, ValidationError, field_validator

from ens_evidence.models.contracts import Strict
from ens_evidence.scope.messages import describe_validation
from ens_evidence.scope.models import Asset, AssetType, Deployment

RECONCILED_TYPES = frozenset({AssetType.LAPTOP, AssetType.SERVER})


class ObservedHost(Strict):
    hostname: str
    asset_id: str | None = None
    serial: str | None = None


SUPPORTED_SCHEMA_VERSIONS = ("1.0",)


class ObservedHosts(Strict):
    schema_version: str = "1.0"
    source: str
    observed_at: AwareDatetime
    hosts: list[ObservedHost]

    @field_validator("schema_version")
    @classmethod
    def _supported(cls, value: str) -> str:
        if value not in SUPPORTED_SCHEMA_VERSIONS:
            supported = ", ".join(SUPPORTED_SCHEMA_VERSIONS)
            raise ValueError(f"versión de esquema no soportada; usa una de: {supported}")
        return value


class DiscrepancyKind(StrEnum):
    NOT_IN_INVENTORY = "not_in_inventory"
    NOT_OBSERVED = "not_observed"


class Discrepancy(Strict):
    kind: DiscrepancyKind
    identity: str


class ReconciliationReport(BaseModel):
    discrepancies: list[Discrepancy]
    input_errors: list[str]


class ObservedInputError(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


def normalize_hostname(hostname: str) -> str:
    return hostname.strip().lower().rstrip(".")


def parse_observed(text: str) -> ObservedHosts:
    """Valida la entrada; agrega los errores por entrada sin imprimir valores."""
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError:
        raise ObservedInputError(
            ["entrada observada: no es YAML ni JSON válido; revisa la sintaxis"]
        ) from None
    try:
        return ObservedHosts.model_validate(raw)
    except ValidationError as exc:
        raise ObservedInputError(describe_validation("entrada observada", exc, raw)) from None


def _identity(host: ObservedHost) -> str:
    return (
        f"asset:{host.asset_id}" if host.asset_id else f"host:{normalize_hostname(host.hostname)}"
    )


def reconcile(assets: list[Asset], observed: ObservedHosts) -> ReconciliationReport:
    errors: list[str] = []
    for index, host in enumerate(observed.hosts):
        if not normalize_hostname(host.hostname) or (
            host.asset_id is not None and not host.asset_id
        ):
            errors.append(
                f"entrada observada › hosts[{index}]: hostname y asset_id no pueden estar vacíos"
            )
    valid = [h for h in observed.hosts if normalize_hostname(h.hostname)]
    counts = Counter(_identity(h) for h in valid)
    errors.extend(
        f"entrada observada: equipo repetido {ident}; deja una sola entrada"
        for ident, n in sorted(counts.items())
        if n > 1
    )

    candidates = [a for a in assets if a.type in RECONCILED_TYPES]
    by_id = {a.id: a for a in candidates}
    by_hostname = {normalize_hostname(a.hostname): a for a in candidates if a.hostname}
    matched: set[str] = set()
    discrepancies: list[Discrepancy] = []
    for host in valid:
        asset = (
            by_id.get(host.asset_id)
            if host.asset_id
            else by_hostname.get(normalize_hostname(host.hostname))
        )
        if asset is None:
            discrepancies.append(
                Discrepancy(kind=DiscrepancyKind.NOT_IN_INVENTORY, identity=_identity(host))
            )
        else:
            matched.add(asset.id)
    discrepancies.extend(
        Discrepancy(kind=DiscrepancyKind.NOT_OBSERVED, identity=f"asset:{a.id}")
        for a in candidates
        if a.deployment is Deployment.DEPLOYED and a.id not in matched
    )
    discrepancies.sort(key=lambda d: (d.kind.value, d.identity))
    return ReconciliationReport(discrepancies=discrepancies, input_errors=errors)
