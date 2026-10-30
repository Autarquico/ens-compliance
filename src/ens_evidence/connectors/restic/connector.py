"""Conector de Restic: solo lectura del repositorio y de los registros del workspace."""

import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Self

import yaml
from pydantic import BaseModel, ValidationError

from ens_evidence.backup.environment import load_secrets
from ens_evidence.backup.errors import BackupError
from ens_evidence.backup.records import (
    IMMUTABILITY_PREFIX,
    ImmutabilityCheck,
    list_checks,
    list_restores,
    list_runs,
)
from ens_evidence.backup.restore_test import RESTORE_TESTS_PREFIX, RestoreTest
from ens_evidence.connectors.base import Connector
from ens_evidence.connectors.restic import sections
from ens_evidence.connectors.restic.config import ResticConfig, ResticRun, WorkspaceRecords
from ens_evidence.connectors.restic.process import Executor, ResticProcess
from ens_evidence.connectors.sections import Section
from ens_evidence.evidence import MANUAL_SOURCE
from ens_evidence.evidence.layout import sha256_hex
from ens_evidence.evidence.manifests import list_manifests
from ens_evidence.models.contracts import CollectionResult
from ens_evidence.models.enums import CollectionStatus
from ens_evidence.scope import load_assets
from ens_evidence.scope.models import AssetType
from ens_evidence.workspace import NotFound, WorkspaceError, WorkspaceStore
from ens_evidence.workspace.settings import BackupSettings, Settings
from ens_evidence.yaml_unique import safe_load_unique

SOURCE = "restic"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ResticConnector(Connector):
    name = SOURCE
    config_model = ResticConfig

    def __init__(
        self,
        *,
        backup: BackupSettings | None = None,
        executor: Executor | None = None,
        clock: Callable[[], datetime] = _utcnow,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._backup = backup
        self._executor = executor
        self._clock = clock
        self._env = os.environ if env is None else env

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        return cls(backup=settings.backup)

    def prepare(self, config: BaseModel, store: WorkspaceStore) -> BaseModel:
        """Valida el activo `backup` del inventario y lee los registros del workspace."""
        assert isinstance(config, ResticConfig)
        assets, errors = load_assets(store)
        if assets is None:
            raise WorkspaceError("No se puede leer el inventario de activos: " + "; ".join(errors))
        asset = next((a for a in assets.assets if a.id == config.asset_id), None)
        if asset is None or asset.type is not AssetType.BACKUP:
            raise WorkspaceError(
                f"asset_id: debe ser un activo backup del inventario ({config.asset_id})."
            )
        inventoried = {a.id for a in assets.assets}
        return ResticRun(**config.model_dump(), records=read_records(store, inventoried))

    def collect(self, config: BaseModel) -> CollectionResult:
        assert isinstance(config, ResticRun)
        now = self._clock()
        collected: dict[str, Section] = {}
        error: str | None = None
        repository_id: str | None = None
        try:
            backup = self._backup_settings()
            execute = self._executor or self._process(backup)
            repository_id = sections.read_repository(execute)
            collected = self._sections(execute, config, backup.restic_repository, repository_id)
        except (sections.CollectionFailed, BackupError) as exc:
            error = str(exc)
        return CollectionResult(
            source=SOURCE,
            collector=self.name,
            collected_at=now,
            asset_id=config.asset_id,
            data={
                "error": error,
                "repository_id": repository_id,
                "thresholds": config.model_dump(
                    mode="json", exclude={"asset_id", "offsite", "records"}
                ),
                "offsite": config.offsite.model_dump(mode="json") if config.offsite else None,
                "sections": {name: s.model_dump(mode="json") for name, s in collected.items()},
            },
            collection_status=CollectionStatus.ERROR if error else CollectionStatus.SUCCESS,
        )

    def _backup_settings(self) -> BackupSettings:
        if self._backup is None:
            raise sections.CollectionFailed("faltan los ajustes backup.restic_repository")
        return self._backup

    def _process(self, backup: BackupSettings) -> Executor:
        secrets = load_secrets(backup.restic_repository, self._env)
        return ResticProcess(
            backup.restic_repository, secrets, base_env=self._env, region=backup.region
        )

    @staticmethod
    def _sections(
        execute: Executor, config: ResticRun, repository: str, repository_id: str | None
    ) -> dict[str, Section]:
        records = config.records
        snapshots = sections.collect_snapshots(execute, records, repository_id, repository)
        return {
            "repository": sections.collect_repository(repository, repository_id),
            "snapshots": snapshots,
            "runs": sections.collect_runs(records, repository_id, repository),
            "integrity_checks": sections.collect_integrity_checks(records, repository_id),
            "restores": sections.collect_restores(records, repository_id),
            "restore_tests": sections.collect_restore_tests(records, repository_id, snapshots),
            "immutability": sections.collect_immutability(records, repository),
        }


def read_records(store: WorkspaceStore, inventoried: set[str] | None = None) -> WorkspaceRecords:
    runs, runs_unreadable = list_runs(store)
    checks, checks_unreadable = list_checks(store)
    restores, restores_unreadable = list_restores(store)
    tests, tests_unreadable, digests = _read_keyed(store, RESTORE_TESTS_PREFIX, RestoreTest)
    manual = {
        (m.artifact.sha256, m.asset_id)
        for m in list_manifests(store).manifests
        if m.source == MANUAL_SOURCE
    }
    immutability, immutability_unreadable, _ = _read_keyed(
        store, IMMUTABILITY_PREFIX, ImmutabilityCheck
    )
    return WorkspaceRecords(
        runs=runs,
        checks=checks,
        restores=restores,
        restore_tests=tests,
        without_manual_evidence=[
            t.test_id for t, d in zip(tests, digests, strict=True) if (d, t.system) not in manual
        ],
        without_inventoried_system=[
            t.test_id for t in tests if inventoried is not None and t.system not in inventoried
        ],
        immutability=immutability,
        unreadable={
            "runs": runs_unreadable,
            "checks": checks_unreadable,
            "restores": restores_unreadable,
            "restore_tests": tests_unreadable,
            "immutability": immutability_unreadable,
        },
    )


def _read_keyed[T: BaseModel](
    store: WorkspaceStore, prefix: str, model: type[T]
) -> tuple[list[T], list[str], list[str]]:
    """Registros legibles, claves ilegibles y el sha256 del fichero de cada registro."""
    records: list[T] = []
    unreadable: list[str] = []
    digests: list[str] = []
    for key in store.list(prefix):
        if "/" in key.removeprefix(f"{prefix}/"):
            continue
        try:
            raw = store.read_bytes(key)
            records.append(model.model_validate(safe_load_unique(raw)))
            digests.append(sha256_hex(raw))
        except (ValidationError, yaml.YAMLError, NotFound):
            unreadable.append(key)
    return records, unreadable, digests
