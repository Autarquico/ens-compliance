"""Conector de GitHub: solo lectura, token solo por entorno."""

import os
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime

import httpx
from pydantic import BaseModel

from ens_evidence.connectors.base import Connector
from ens_evidence.connectors.github import sections
from ens_evidence.connectors.github.client import GitHubClient, GitHubError, verify_token
from ens_evidence.connectors.github.config import GitHubConfig, GitHubRun
from ens_evidence.connectors.sections import Section
from ens_evidence.models.contracts import CollectionResult
from ens_evidence.models.enums import CollectionStatus
from ens_evidence.scope import load_assets
from ens_evidence.scope.models import AssetType
from ens_evidence.workspace import WorkspaceError, WorkspaceStore

TOKEN_ENV = "ENS_GITHUB_TOKEN"
SOURCE = "github"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class GitHubConnector(Connector):
    name = SOURCE
    config_model = GitHubConfig

    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], datetime] = _utcnow,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._transport = transport
        self._clock = clock
        self._env = os.environ if env is None else env

    def prepare(self, config: BaseModel, store: WorkspaceStore) -> BaseModel:
        """Toma los repositorios esperados del activo `code_hosting` del inventario."""
        assert isinstance(config, GitHubConfig)
        assets, errors = load_assets(store)
        if assets is None:
            raise WorkspaceError("No se puede leer el inventario de activos: " + "; ".join(errors))
        asset = next((a for a in assets.assets if a.id == config.asset_id), None)
        if asset is None or asset.type is not AssetType.CODE_HOSTING:
            raise WorkspaceError(
                f"asset_id: debe ser un activo code_hosting del inventario ({config.asset_id})."
            )
        return GitHubRun(
            **config.model_dump(),
            inventory_repos=inventory_repo_names(asset.repos or [], config.organization),
        )

    def collect(self, config: BaseModel) -> CollectionResult:
        assert isinstance(config, GitHubRun)
        now = self._clock()
        collected: dict[str, Section] = {}
        error: str | None = None
        token = self._env.get(TOKEN_ENV, "").strip()
        if not token:
            error = f"{TOKEN_ENV} no está definida"
        elif not (token.isascii() and token.isprintable()):
            error = f"{TOKEN_ENV} contiene caracteres no válidos"
        else:
            try:
                with GitHubClient(token, transport=self._transport) as client:
                    self._run(client, config, collected)
            except GitHubError as exc:
                error = exc.message
        return CollectionResult(
            source=SOURCE,
            collector=self.name,
            collected_at=now,
            asset_id=config.asset_id,
            data={
                "organization": config.organization,
                "error": error,
                "sections": {name: s.model_dump(mode="json") for name, s in collected.items()},
            },
            collection_status=CollectionStatus.ERROR if error else CollectionStatus.SUCCESS,
        )

    @staticmethod
    def _run(client: GitHubClient, config: GitHubRun, collected: dict[str, Section]) -> None:
        """Verifica el token al inicio y al final; una revocación a mitad invalida el conjunto."""
        verify_token(client)
        collected["organization"] = sections.collect_organization(client, config)
        collected["app_installations"] = sections.collect_app_installations(client, config)
        repos = collected["repositories"] = sections.collect_repositories(client, config)
        collected["secret_scanning"] = sections.collect_secret_scanning(repos)
        collected["branch_rules"] = sections.collect_branch_rules(client, config, repos)
        collected["dependabot"] = sections.collect_dependabot(client, config, repos)
        collected["collaborators"] = sections.collect_collaborators(client, config, repos)
        collected["environments"] = sections.collect_environments(client, config, repos)
        collected["actions"] = sections.collect_actions(client, config, repos)
        collected["deploy_keys"] = sections.collect_deploy_keys(client, config, repos)
        verify_token(client)


def inventory_repo_names(entries: Iterable[str], organization: str) -> list[str]:
    """Nombres de repositorio del inventario: `repo` o `organización/repo`; uno de otra
    organización no es válido."""
    names = []
    for entry in entries:
        owner, separator, name = entry.rpartition("/")
        if separator and owner.lower() != organization.lower():
            raise WorkspaceError(f"repos: {entry} no pertenece a la organización {organization}.")
        names.append(name)
    return names
