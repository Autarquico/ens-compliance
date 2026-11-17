"""Copias: lo que consta de los repositorios de `connectors/backups.yaml`, sin consultarlos.

Solo lee: el último historial de checks del conector `backups`, la copia propia de la caja
(`backup status`) y las pruebas de restauración declaradas. No lanza copias ni restauraciones, no
llama a restic ni a Backrest, y no recalcula ningún check: el veredicto es la fila registrada.
La plantilla recibe solo los DTO de este módulo; nunca `CheckResult.reason`, `BackupRun.error`, la
URI del repositorio ni el texto de una excepción (pueden citar valores).
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import yaml
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import BaseRoute, Route

from ens_evidence.backup.records import BackupRun, RestoreRecord
from ens_evidence.backup.restore_test import RestoreTest
from ens_evidence.backup.runner import backup_status
from ens_evidence.checks.store import RunScan, scan_runs
from ens_evidence.connectors.backups.checks import (
    INTEGRITY,
    OFFSITE,
    RECENT,
    REPOSITORY_ACCESSIBLE,
    RESTORE_TEST,
)
from ens_evidence.connectors.backups.config import BackupsConfig, RepositoryConfig
from ens_evidence.connectors.backups.connector import SOURCE
from ens_evidence.connectors.config import (
    ConnectorConfigError,
    connector_config_key,
    load_connector_config,
)
from ens_evidence.connectors.restic.connector import read_records
from ens_evidence.evidence import ManifestError, load_manifest_by_id
from ens_evidence.evidence.layout import artifact_key
from ens_evidence.models.enums import CheckStatus
from ens_evidence.scope.inventory import load_assets
from ens_evidence.scope.models import Asset
from ens_evidence.ui.pages import page_context, render
from ens_evidence.ui.roles import ROLE_LABELS
from ens_evidence.ui.store import UiStore
from ens_evidence.ui.views import format_instant
from ens_evidence.ui.views.check_history import (
    ConnectorResults,
    Contribution,
    EvidenceSummary,
    detail_results,
    latest_results,
    summary,
    unreadable_notes,
)
from ens_evidence.ui.views.tools import open_url
from ens_evidence.workspace import WorkspaceError

KIT = "copias"
RUNBOOK_HREF = "/ayuda/kit-copias"
WIZARD_HREF = "/herramientas/copias"
ASSET_PATH = "/inventario/activos"
MAX_ARTIFACT_BYTES = 4 * 1024 * 1024
CONFIG_INVALID = "connectors/backups.yaml no es válido"
CONFIG_UNREADABLE = "connectors/backups.yaml no se puede leer"
ASSET_MISSING = "El activo no está en el inventario"
BOX_NOTE = "Es lo mismo que muestra `backup status`."
CHECK_LABELS = {
    RECENT: "BACKUP-001 Copia reciente",
    REPOSITORY_ACCESSIBLE: "BACKUP-003 Repositorio accesible",
    INTEGRITY: "BACKUP-004 Integridad",
    RESTORE_TEST: "BACKUP-005 Prueba de restauración",
    OFFSITE: "BACKUP-006 Copia en otro lugar",
}
RUN_LABELS = {"completed": "Completada", "failed": "Fallida", "started": "Iniciada, sin terminar"}
GUIDE_STEPS = (
    "En Backrest, restaura una carpeta concreta del snapshot en una carpeta de prueba dentro "
    "de `/data`.",
    "Copia lo restaurado a `/data/prueba-restauracion/restaurado`, fuera del workspace.",
    "Escribe tú el fichero de la prueba con tu nombre: la herramienta no rellena el revisor.",
    "Regístrala con la orden de abajo: compara lo restaurado con el original fichero a fichero.",
    "Borra la carpeta de prueba cuando acabes.",
)
_WORST_STATUSES = (CheckStatus.FAIL, CheckStatus.ERROR)


# --- DTO -----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Thresholds:
    max_backup_age_hours: int
    restore_test_validity_days: int
    max_check_age_days: int


@dataclass(frozen=True)
class LastSnapshot:
    at: str
    age_hours: int


@dataclass(frozen=True)
class RestoreDeclaration:
    """Lo declarado por una persona; el veredicto es la fila BACKUP-005, no esto."""

    test_id: str
    performed_at: str
    reviewer: str
    role_label: str
    valid_until: str
    approved: bool
    expired: bool


@dataclass(frozen=True)
class Guide:
    command: str
    yaml: str
    steps: tuple[str, ...]


@dataclass(frozen=True)
class RepositoryBlock:
    id: str
    asset_id: str
    asset_href: str | None
    asset_note: str | None
    frequency: str | None
    thresholds: Thresholds
    last_snapshot: LastSnapshot | None  # solo si sale del artefacto citado por BACKUP-001
    checked_at: str | None  # la ejecución de BACKUP-001; nunca es la fecha de una copia
    evidence: EvidenceSummary | None
    results: tuple[ConnectorResults, ...]
    check_labels: dict[str, str]
    problem: bool
    restore_test: RestoreDeclaration | None
    guide: Guide
    tests_unattributable: bool = (
        False  # hay pruebas registradas que no se saben de este repositorio
    )


@dataclass(frozen=True)
class BoxRun:
    run_id: str
    status: str
    status_label: str
    started_at: str
    finished_at: str | None
    snapshot_id: str | None


@dataclass(frozen=True)
class InterruptedRun:
    run_id: str
    started_at: str


@dataclass(frozen=True)
class BoxBackup:
    last: BoxRun | None
    interrupted: tuple[InterruptedRun, ...]
    unreadable_count: int
    note: str


@dataclass(frozen=True)
class HistoryRow:
    test_id: str
    repository_label: str
    performed_at: str
    matched: str  # "sí" | "no" | "sin verificar"
    reviewer: str
    role_label: str
    valid_until: str


@dataclass(frozen=True)
class CopiasPage:
    configured: bool
    config_error: str | None
    backrest_href: str | None
    wizard_href: str
    runbook_href: str
    history_notes: tuple[str, ...]
    repositories: tuple[RepositoryBlock, ...]
    box: BoxBackup
    history: tuple[HistoryRow, ...]
    unreadable_tests: int
    generated_at: str


# --- configuración -------------------------------------------------------------------------------


def load_repositories(store: UiStore) -> tuple[bool, str | None, tuple[RepositoryConfig, ...]]:
    """`(configured, config_error, repositorios)`; los mensajes son fijos, nunca la excepción."""
    try:
        if not store.exists(connector_config_key(SOURCE)):
            return False, None, ()
        config = load_connector_config(store, SOURCE, BackupsConfig)
    except ConnectorConfigError:
        return True, CONFIG_INVALID, ()
    except WorkspaceError:
        return True, CONFIG_UNREADABLE, ()
    return True, None, tuple(config.repositories)


# --- último snapshot (artefacto citado por BACKUP-001) -------------------------------------------


def latest_snapshot_time(
    store: UiStore,
    evidence_ids: list[str],
    repository: RepositoryConfig,
    subjects: set[str],
    now: datetime,
) -> datetime | None:
    """El snapshot más reciente que lista la evidencia citada por BACKUP-001, o `None`.

    Solo se da por bueno un artefacto del workspace, direccionado por su hash, de la fuente
    `backups` y con una entrada cuyo `id`, `asset_id` y `repository_id` son los del bloque (el
    último, el sujeto de BACKUP-001); ante cualquier duda, `None` (se cae a la fecha del check).
    El tamaño se comprueba antes de hashear y de interpretar el JSON.
    """
    for evidence_id in evidence_ids:
        found = _snapshot_time(store, evidence_id, repository, subjects, now)
        if found is not None:
            return found
    return None


def _snapshot_time(
    store: UiStore,
    evidence_id: str,
    repository: RepositoryConfig,
    subjects: set[str],
    now: datetime,
) -> datetime | None:
    try:
        manifest = load_manifest_by_id(store, evidence_id)
        artifact = manifest.artifact
        if (
            manifest.source != SOURCE
            or manifest.collector is None
            or manifest.collector.name != SOURCE
            or artifact.storage != "workspace"
            or artifact.key != artifact_key(artifact.sha256)
        ):
            return None
        raw = store.read_bytes(artifact.key, MAX_ARTIFACT_BYTES + 1)
        if len(raw) > MAX_ARTIFACT_BYTES or hashlib.sha256(raw).hexdigest() != artifact.sha256:
            return None
        return _newest(json.loads(raw), repository, subjects, now)
    except (ManifestError, WorkspaceError, ValueError, TypeError, KeyError, AttributeError):
        return None


def _newest(
    document: Any, repository: RepositoryConfig, subjects: set[str], now: datetime
) -> datetime | None:
    if document["source"] != SOURCE or document["collector"] != SOURCE:
        return None
    for entry in document["data"]["repositories"]:
        if (
            entry.get("id") == repository.id
            and entry.get("asset_id") == repository.asset_id
            and entry.get("repository_id") in subjects
        ):
            items = entry["sections"]["snapshots"]["items"]
            times = [t for t in map(_instant, (i.get("time") for i in items)) if t and t <= now]
            return max(times, default=None)
    return None


def _instant(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment.astimezone(UTC) if moment.tzinfo else None


# --- pruebas de restauración ---------------------------------------------------------------------


def _declaration(test: RestoreTest, now: datetime) -> RestoreDeclaration:
    return RestoreDeclaration(
        test_id=test.test_id,
        performed_at=format_instant(test.performed_at),
        reviewer=test.review.reviewer,
        role_label=ROLE_LABELS[test.review.role],
        valid_until=format_instant(test.review.valid_until),
        approved=test.review.approved,
        expired=test.review.valid_until <= now,
    )


def _matched(test: RestoreTest, restores: dict[str, RestoreRecord]) -> str:
    record = restores.get(test.restore_id)
    if (
        record is None
        or record.status != "completed"
        or record.repository_id != test.repository_id
        or record.snapshot_id != test.backup.snapshot_id
    ):
        return "sin verificar"
    if record.inventory_verified is True and record.mismatches == 0:
        return "sí"
    return "no"


def _history(
    tests: list[RestoreTest], restores: dict[str, RestoreRecord], attributed: dict[str, str]
) -> tuple[HistoryRow, ...]:
    return tuple(
        HistoryRow(
            test_id=t.test_id,
            repository_label=attributed.get(t.repository_id)
            or f"sin atribuir (`{t.repository_id[:8]}`)",
            performed_at=format_instant(t.performed_at),
            matched=_matched(t, restores),
            reviewer=t.review.reviewer,
            role_label=ROLE_LABELS[t.review.role],
            valid_until=format_instant(t.review.valid_until),
        )
        for t in sorted(tests, key=lambda t: t.performed_at, reverse=True)
    )


# --- guía ----------------------------------------------------------------------------------------


def _guide(repository_id: str, asset_id: str) -> Guide:
    declaration = {
        "test_id": "RT-AAAA-NN",
        "system": asset_id,
        "performed_at": "<cuándo restauraste, con zona horaria>",
        "isolated_target": True,
        "backup": {"snapshot_id": "<64 caracteres>"},
        "result": {
            "restored": True,
            "integrity_verified": True,
            "application_started": "not_applicable",
            "application_justification": "Solo se restauran ficheros",
        },
        "review": {
            "approved": True,
            "reviewer": "<nombre y apellidos de quien lo declara>",
            "role": "security_officer",
            "reviewed_at": "<cuándo lo revisaste, con zona horaria>",
            "valid_until": "<hasta cuándo vale, con zona horaria>",
        },
    }
    command = (
        "ens-evidence backup restore-test record-kit /data/prueba-restauracion/prueba.yaml "
        f"--repository {repository_id} \\\n"
        "  --restored /data/prueba-restauracion/restaurado/<carpeta> "
        "--original /data/<la misma carpeta en la caja>"
    )
    return Guide(
        command=command,
        yaml=yaml.safe_dump(declaration, sort_keys=False, allow_unicode=True),
        steps=GUIDE_STEPS,
    )


# --- bloques -------------------------------------------------------------------------------------


def _asset_link(asset_id: str, assets: dict[str, Asset]) -> tuple[str | None, str | None]:
    if asset_id in assets:
        return f"{ASSET_PATH}/{quote(asset_id, safe='')}", None
    return None, ASSET_MISSING


def _block(
    store: UiStore,
    repository: RepositoryConfig,
    scan: RunScan,
    contributions: list[Contribution],
    assets: dict[str, Asset],
    tests: list[RestoreTest],
    unreadable_tests: int,
    now: datetime,
) -> tuple[RepositoryBlock, set[str]]:
    results = [r for c in contributions for r in c.results]
    # BACKUP-005 usa el `test_id` como sujeto: no identifica el repositorio.
    subjects = {r.subject for r in results if r.subject and r.check_id != RESTORE_TEST}
    recent = [(c, r) for c in contributions for r in c.results if r.check_id == RECENT]
    recent_subjects = {r.subject for _, r in recent if r.subject}
    snapshot_at = latest_snapshot_time(
        store, [e for _, r in recent for e in r.evidence_ids], repository, recent_subjects, now
    )
    own_tests = [t for t in tests if t.repository_id in subjects]
    unattributable = (
        not own_tests
        and bool(tests or unreadable_tests)
        and (not subjects or bool(scan.unreadable))
    )
    asset = assets.get(repository.asset_id)
    href, note = _asset_link(repository.asset_id, assets)
    block = RepositoryBlock(
        id=repository.id,
        asset_id=repository.asset_id,
        asset_href=href,
        asset_note=note,
        frequency=asset.frequency if asset else None,
        thresholds=Thresholds(
            max_backup_age_hours=repository.max_backup_age_hours,
            restore_test_validity_days=repository.restore_test_validity_days,
            max_check_age_days=repository.max_check_age_days,
        ),
        last_snapshot=_last_snapshot(snapshot_at, now),
        checked_at=format_instant(recent[0][0].run.finished_at) if recent else None,
        evidence=summary(scan, contributions) if contributions else None,
        results=detail_results(contributions),
        check_labels=dict(CHECK_LABELS),
        problem=any(r.status in _WORST_STATUSES for r in results),
        restore_test=_declaration(max(own_tests, key=lambda t: t.performed_at), now)
        if own_tests
        else None,
        guide=_guide(repository.id, repository.asset_id),
        tests_unattributable=unattributable,
    )
    return block, subjects


def _last_snapshot(at: datetime | None, now: datetime) -> LastSnapshot | None:
    if at is None:
        return None
    return LastSnapshot(format_instant(at), int((now - at).total_seconds() // 3600))


def _box(store: UiStore) -> BoxBackup:
    status = backup_status(store)
    return BoxBackup(
        last=_box_run(status.last) if status.last else None,
        interrupted=tuple(
            InterruptedRun(r.run_id, format_instant(r.started_at)) for r in status.interrupted
        ),
        unreadable_count=len(status.unreadable),
        note=BOX_NOTE,
    )


def _box_run(run: BackupRun) -> BoxRun:
    return BoxRun(
        run_id=run.run_id,
        status=run.status,
        status_label=RUN_LABELS[run.status],
        started_at=format_instant(run.started_at),
        finished_at=format_instant(run.finished_at) if run.finished_at else None,
        snapshot_id=run.snapshot_id,
    )


# --- página --------------------------------------------------------------------------------------


def build_page(store: UiStore, kit_host: str | None, now: datetime) -> CopiasPage:
    configured, config_error, repositories = load_repositories(store)
    scan = scan_runs(store, SOURCE)
    found = latest_results(scan)
    assets_file, _ = load_assets(store)
    assets = {a.id: a for a in assets_file.assets} if assets_file else {}
    records = read_records(store)
    restores = {r.restore_id: r for r in records.restores}
    blocks: list[RepositoryBlock] = []
    attributed: dict[str, str] = {}
    for repository in repositories:
        block, subjects = _block(
            store,
            repository,
            scan,
            found.get(repository.asset_id, []),
            assets,
            records.restore_tests,
            len(records.unreadable.get("restore_tests", [])),
            now,
        )
        blocks.append(block)
        attributed |= {subject: repository.id for subject in subjects}
    return CopiasPage(
        configured=configured,
        config_error=config_error,
        backrest_href=open_url(kit_host),
        wizard_href=WIZARD_HREF,
        runbook_href=RUNBOOK_HREF,
        history_notes=unreadable_notes(scan),
        repositories=tuple(blocks),
        box=_box(store),
        history=_history(records.restore_tests, restores, attributed),
        unreadable_tests=len(records.unreadable.get("restore_tests", [])),
        generated_at=format_instant(now),
    )


def listado(request: Request) -> Response:
    page = build_page(
        request.app.state.store,
        request.app.state.config.kit_hosts.get(KIT),
        request.app.state.clock(),
    )
    return render(
        request,
        "views/copias.html",
        **page_context(request, "copias", "Copias"),
        page=page,
    )


COPIAS_ROUTES: list[BaseRoute] = [Route("/copias", listado, methods=["GET"])]
