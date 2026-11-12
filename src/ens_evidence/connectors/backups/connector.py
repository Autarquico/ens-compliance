"""Conector `backups`: lee N repositorios restic con `--no-lock`, uno por sección de la evidencia.

Un solo `CollectionResult`; cada repositorio lleva su `asset_id`, su estado de recogida y sus
secciones. Sin credenciales en el entorno el repositorio queda `ERROR` (nunca PASS, regla 5).
"""

import json
import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Self

from pydantic import BaseModel

from ens_evidence.backup.environment import load_secrets
from ens_evidence.backup.errors import BackupError
from ens_evidence.backup.restic import READ_ONLY
from ens_evidence.connectors.backups.config import BackupsConfig, BackupsRun, RepositoryConfig
from ens_evidence.connectors.base import Connector
from ens_evidence.connectors.restic import sections
from ens_evidence.connectors.restic.connector import read_records
from ens_evidence.connectors.restic.process import (
    ALLOWED,
    LIST_ALL_SNAPSHOTS,
    Executor,
    ResticProcess,
    ResticUnavailable,
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
CHECK_TIMEOUT_SECONDS = 3600


def check_command(read_data_percent: int) -> tuple[str, ...]:
    """`restic check` sin lock: solo estructura con 0 %, o esa parte de los datos."""
    subset = (f"--read-data-subset={read_data_percent}%",) if read_data_percent else ()
    return (*READ_ONLY, "check", "--json", *subset)


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
            collected["integrity_checks"] = self._integrity(
                execute, repository.min_check_read_data_percent, repository_id
            )
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

    def repository_id(self, repository: RepositoryConfig) -> str:
        """Id restic del repositorio, para atar un registro suyo; `BackupError` si no se lee."""
        try:
            found = sections.read_repository(self._execute(repository))
        except sections.CollectionFailed as exc:
            raise BackupError(str(exc)) from None
        except ResticUnavailable as exc:
            raise BackupError(f"restic no disponible ({exc.kind})") from None
        if found is None:
            raise BackupError(f"el repositorio {repository.id} no existe todavía")
        return found

    def _execute(self, repository: RepositoryConfig) -> Executor:
        if self._executor is not None:
            return self._executor(repository)
        secrets = load_secrets(repository.repository, self._env, repository.env_prefix)
        return ResticProcess(
            repository.repository,
            secrets,
            base_env=self._env,
            region=self._region,
            allowed=self._allowed(repository),
            timeout=CHECK_TIMEOUT_SECONDS,
        )

    @staticmethod
    def _allowed(repository: RepositoryConfig) -> frozenset[tuple[str, ...]]:
        percent = repository.min_check_read_data_percent
        return READ_ALL if percent is None else READ_ALL | {check_command(percent)}

    def _integrity(
        self, execute: Executor, percent: int | None, repository_id: str | None
    ) -> Section:
        """`restic check --no-lock` de esta recogida; sin umbral configurado no se ejecuta.

        La evidencia es el propio resultado (código de salida y recuentos), con inicio y fin.
        """
        if percent is None or repository_id is None:
            return Section(status=CollectionStatus.SUCCESS, summary={"count": 0, "unreadable": []})
        started = self._clock()
        try:
            result = execute(check_command(percent))
        except ResticUnavailable as exc:
            return Section(
                status=CollectionStatus.ERROR, reason=f"restic no disponible ({exc.kind})"
            )
        finished = self._clock()
        errors, broken = _check_counts(result.stdout)
        passed = result.returncode == 0
        item = {
            "check_id": f"{SOURCE}-live-{sections.instant(started)}",
            "status": "completed" if passed else "failed",
            "started_at": sections.instant(started),
            "finished_at": sections.instant(finished),
            "scope": f"subset:{percent}%" if percent else "metadata",
            "read_data_percent": percent,
            "exit_code": result.returncode,
            "num_errors": errors if errors is not None else (0 if passed else None),
            "broken_packs": broken,
            "failure": None,
            "repository_id": repository_id,
            "bound": True,
        }
        return Section(
            status=CollectionStatus.SUCCESS,
            items=[item],
            summary={"count": 1, "unreadable": []},
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
            "restores": sections.collect_restores(records, repository_id),
            "restore_tests": sections.collect_restore_tests(records, repository_id, snapshots),
        }


def _check_counts(stdout: str) -> tuple[int | None, int | None]:
    """`num_errors` y `broken_packs` del último mensaje `summary` de `restic check --json`."""
    for line in reversed(stdout.splitlines()):
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if isinstance(message, dict) and message.get("message_type") == "summary":
            return _count(message.get("num_errors")), _broken(message.get("broken_packs"))
    return None, None


def _count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _broken(value: object) -> int | None:
    return 0 if value is None else len(value) if isinstance(value, list) else None
