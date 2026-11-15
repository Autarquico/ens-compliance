"""Evaluación HOST-001…009 sobre el workspace: configuración, inventario y evidencia."""

from dataclasses import dataclass
from datetime import datetime

from ens_evidence.agent.registry import RegistryError, load_registry
from ens_evidence.connectors.config import load_connector_config
from ens_evidence.endpoint.agent_view import Admission
from ens_evidence.endpoint.checks import evaluate_host_checks
from ens_evidence.endpoint.config import CONFIG_NAME, EndpointConfig
from ens_evidence.endpoint.evidence import load_host_evidence
from ens_evidence.models.contracts import CheckResult
from ens_evidence.models.enums import CheckStatus
from ens_evidence.scope.inventory import load_assets
from ens_evidence.workspace import WorkspaceError, WorkspaceStore

EVIDENCE_EXPIRED = "evidence_expired"


@dataclass(frozen=True)
class HostEvaluation:
    results: list[CheckResult]

    @property
    def expired_assets(self) -> list[str]:
        """Activos cuya última ejecución caducó: bloquearían la preparación."""
        return sorted(
            {result.asset_id for result in self.results if result.reason_code == EVIDENCE_EXPIRED}
        )

    @property
    def has_failures(self) -> bool:
        return any(result.status is CheckStatus.FAIL for result in self.results)


def evaluate_workspace(store: WorkspaceStore, evaluated_at: datetime) -> HostEvaluation:
    config = load_connector_config(store, CONFIG_NAME, EndpointConfig)
    assets, errors = load_assets(store)
    if assets is None:
        raise WorkspaceError(f"no se puede leer el inventario: {'; '.join(errors)}")
    loaded = load_host_evidence(store)
    unreadable = list(loaded.unreadable)
    admissions: dict[str, Admission] = {}
    try:
        registry = load_registry(store)
    except RegistryError as exc:
        unreadable.append(str(exc))
    else:
        admissions = {
            asset_id: Admission(record.last_admission_at, record.generation)
            for asset_id, record in registry.agents.items()
            if not record.revoked
        }
    return HostEvaluation(
        evaluate_host_checks(
            assets.assets,
            loaded.items,
            config,
            evaluated_at,
            unreadable,
            agent_evidence=loaded.agent,
            admissions=admissions,
        )
    )
