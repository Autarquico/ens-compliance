"""La copia tal como la configura la organización: preparación, registro de fallos y ejecución.

Compartida por `backup run` y por el ciclo. Los fallos esperables son un resultado, nunca una
excepción, y el resultado solo lleva códigos: ni valores de credenciales ni mensajes de error.
El llamador ya tiene el lock del ciclo; aquí se recibe su contexto y no se vuelve a tomar.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from ens_evidence.backup.environment import BackupSecrets, load_secrets
from ens_evidence.backup.errors import BackupError
from ens_evidence.backup.records import BackupRun, list_runs, run_key
from ens_evidence.backup.restic import Restic
from ens_evidence.backup.runner import Clock, run_backup
from ens_evidence.backup.setup_failure import SetupFailureRecorder, failed_run_recorder
from ens_evidence.workspace import WorkspaceError, WorkspaceStore
from ens_evidence.workspace.cycle_lock import CycleContext
from ens_evidence.workspace.settings import BackupSettings

BackupFailure = Literal[
    "invalid_credentials",
    "previous_run_unfinished",
    "workspace_unreadable",
    "restic_failed",
    "record_not_persisted",
]


@dataclass(frozen=True)
class BackupOutcome:
    status: Literal["ok", "skipped_not_configured", "failed"]
    failure: BackupFailure | None = None
    run: BackupRun | None = None

    @property
    def reason(self) -> BackupFailure | Literal["not_configured"] | None:
        """Por qué no hay copia: el fallo, `not_configured` o None si se hizo."""
        if self.status == "skipped_not_configured":
            return "not_configured"
        return self.failure

    @property
    def run_key(self) -> str | None:
        return run_key(self.run.run_id) if self.run is not None else None


def prepare_restic(
    backup: BackupSettings,
    env: Mapping[str, str] | None = None,
    *,
    backup_timeout: float | None = None,
) -> tuple[Restic, BackupSecrets]:
    """Restic y sus secretos; `BackupError` si faltan o se comparten con el workspace."""
    secrets = load_secrets(backup.restic_repository, env)
    restic = Restic(
        backup.restic_repository,
        secrets,
        base_env=env,
        region=backup.region,
        backup_timeout=backup_timeout,
    )
    return restic, secrets


def optional_repository_id(restic: Restic) -> str | None:
    """Id del repositorio, o None si no se puede leer (el registro lo dejará sin id)."""
    try:
        return restic.repository_id()
    except BackupError:
        return None


def run_configured_backup(
    store: WorkspaceStore,
    backup: BackupSettings | None,
    env: Mapping[str, str],
    *,
    clock: Clock,
    context: CycleContext,
    timeout_seconds: float | None = None,
) -> BackupOutcome:
    """Hace la copia con los ajustes y el entorno dados; no lanza por fallos esperables.

    Los intentos que no llegan a restic (sin ajustes, credenciales inválidas) quedan como
    `failed` con la hora de `clock`, para que el último intento no sea el éxito anterior. Una
    ejecución `started` sin finalizar impide lanzar otra. `timeout_seconds` acota la copia de
    restic: vencido, queda como `restic_failed`.
    """
    recorder = failed_run_recorder(clock)
    if backup is None:
        _record(recorder, store, "", "invalid_settings")
        return BackupOutcome("skipped_not_configured")
    try:
        restic, secrets = prepare_restic(backup, env, backup_timeout=timeout_seconds)
    except BackupError:
        _record(recorder, store, backup.restic_repository, "invalid_credentials")
        return BackupOutcome("failed", "invalid_credentials")
    try:
        runs, _unreadable = list_runs(store)
    except (WorkspaceError, OSError):
        return BackupOutcome("failed", "workspace_unreadable")
    if any(run.status == "started" for run in runs):
        return BackupOutcome("failed", "previous_run_unfinished")
    try:
        run = run_backup(
            store, restic, backup.restic_repository, secrets, clock, optional_repository_id(restic)
        )
    except (WorkspaceError, OSError):
        return BackupOutcome("failed", "record_not_persisted")
    if run.status == "failed":
        return BackupOutcome("failed", "restic_failed", run)
    return BackupOutcome("ok", run=run)


def _record(
    recorder: SetupFailureRecorder,
    store: WorkspaceStore,
    repository: str,
    failure: Literal["invalid_credentials", "invalid_settings"],
) -> None:
    """Deja el intento como `failed`; si el workspace no deja escribir, no hay dónde."""
    try:
        recorder(store, repository, failure)
    except (WorkspaceError, OSError):
        return
