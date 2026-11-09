"""Conector `backups`: lee N repositorios restic con `--no-lock`, uno por sección de la evidencia.

Un solo `CollectionResult`; cada repositorio lleva su `asset_id`, su estado de recogida y sus
secciones. Sin credenciales en el entorno el repositorio queda `ERROR` (nunca PASS, regla 5).
"""

import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Self

from pydantic import BaseModel

from ens_evidence.backup.environment import load_secrets
from ens_evidence.backup.errors import BackupError
from ens_evidence.connectors.backups.config import BackupsConfig, BackupsRun, RepositoryConfig
from ens_evidence.connectors.base import Connector
from ens_evidence.connectors.restic import sections
from ens_evidence.connectors.restic.connector import read_records
from ens_evidence.connectors.restic.process import (
    ALLOWED,
    LIST_ALL_SNAPSHOTS,
    Executor,
    ResticProcess,
)
from ens_evidence.connectors.sections import Section, worst
from ens_evidence.models.contracts import CollectionResult
from ens_evidence.models.enums import CollectionStatus
from ens_evidence.scope import load_assets
from ens_evidence.scope.models import AssetType
from ens_evidence.workspace import WorkspaceError, WorkspaceStore
from ens_evidence.workspace.settings import Settings

SOURCE = "backups"
READ_ALL = frozenset({*ALLOWED, LIST_ALL_SNAPSHOTS})

ExecutorFactory = Callable[[RepositoryConfig], Executor]


def _utcnow() -> datetime:
    return datetime.now(UTC)


class BackupsConnector(Connector):
    name = SOURCE
    config_model = BackupsConfig

    def __init__(
        self,
        *,
        region: str | None = None,
        executor: ExecutorFactory | None = None,
        clock: Callable[[], datetime] = _utcnow,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._region = region
        self._executor = executor
        self._clock = clock
        self._env = os.environ if env is None else env

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        return cls(region=settings.backup.region if settings.backup else None)

    def prepare(self, config: BaseModel, store: WorkspaceStore) -> BaseModel:
        """Valida que cada `asset_id` sea un activo `backup` y lee los registros del workspace."""
        assert isinstance(config, BackupsConfig)
        assets, errors = load_assets(store)
        if assets is None:
            raise WorkspaceError("No se puede leer el inventario de activos: " + "; ".join(errors))
        backups = {a.id for a in assets.assets if a.type is AssetType.BACKUP}
        for repository in config.repositories:
            if repository.asset_id not in backups:
                raise WorkspaceError(
                    f"repositories.{repository.id}.asset_id: debe ser un activo backup "
                    f"del inventario ({repository.asset_id})."
                )
        inventoried = {a.id for a in assets.assets}
        return BackupsRun(**config.model_dump(), records=read_records(store, inventoried))

    def collect(self, config: BaseModel) -> CollectionResult:
        assert isinstance(config, BackupsRun)
        entries = [
            self._collect_repository(repository, config) for repository in config.repositories
        ]
        return CollectionResult(
            source=SOURCE,
            collector=self.name,
            collected_at=self._clock(),
            asset_id=SOURCE,
            data={"repositories": entries},
            collection_status=worst(CollectionStatus(e["collection_status"]) for e in entries),
        )

    def _collect_repository(self, repository: RepositoryConfig, config: BackupsRun) -> dict:
        collected: dict[str, Section] = {}
        error: str | None = None
        repository_id: str | None = None
        try:
            execute = self._execute(repository)
            repository_id = sections.read_repository(execute)
            collected = self._sections(execute, config, repository.repository, repository_id)
        except (sections.CollectionFailed, BackupError) as exc:
            error = str(exc)
        return {
            "id": repository.id,
            "asset_id": repository.asset_id,
            "collection_status": (CollectionStatus.ERROR if error else CollectionStatus.SUCCESS),
            "error": error,
            "repository_id": repository_id,
            "thresholds": repository.model_dump(
                mode="json",
                exclude={"id", "asset_id", "repository", "offsite"},
                exclude_none=True,
            ),
            "offsite": repository.offsite.model_dump(mode="json") if repository.offsite else None,
            "sections": {name: s.model_dump(mode="json") for name, s in collected.items()},
        }

    def _execute(self, repository: RepositoryConfig) -> Executor:
        if self._executor is not None:
            return self._executor(repository)
        secrets = load_secrets(repository.repository, self._env, repository.env_prefix)
        return ResticProcess(
            repository.repository,
            secrets,
            base_env=self._env,
            region=self._region,
            allowed=READ_ALL,
        )

    @staticmethod
    def _sections(
        execute: Executor, config: BackupsRun, repository: str, repository_id: str | None
    ) -> dict[str, Section]:
        records = config.records
        snapshots = sections.collect_snapshots(
            execute, records, repository_id, repository, LIST_ALL_SNAPSHOTS
        )
        return {
            "repository": sections.collect_repository(repository, repository_id),
            "snapshots": snapshots,
            "runs": sections.collect_runs(records, repository_id, repository),
            "integrity_checks": sections.collect_integrity_checks(records, repository_id),
            "restores": sections.collect_restores(records, repository_id),
            "restore_tests": sections.collect_restore_tests(records, repository_id, snapshots),
        }
