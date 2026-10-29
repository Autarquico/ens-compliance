"""Secciones normalizadas de la recogida de Restic.

Cada sección tiene su `CollectionStatus`. Los registros del workspace se informan
todos, cada uno con su `repository_id` y si está atado (`bound`) al repositorio actual;
los ilegibles se listan en `summary.unreadable`, nunca se omiten. Solo hay datos
estructurados: ni rutas, ni hostnames, ni salida de restic.
"""

import json
import re
from collections import Counter
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from ens_evidence.backup.records import BackupRun, RestoreRecord
from ens_evidence.backup.restic import TAG, parse_repository_id
from ens_evidence.backup.restore_test import RestoreTest, performed_after_restore_problem
from ens_evidence.connectors.restic.config import WorkspaceRecords
from ens_evidence.connectors.restic.process import (
    CAT_CONFIG,
    LIST_SNAPSHOTS,
    Executor,
    ResticUnavailable,
)
from ens_evidence.connectors.sections import Section
from ens_evidence.models.enums import CollectionStatus

REPOSITORY_MISSING = 10
WRONG_PASSWORD = 12
REMOTE_SCHEMES = frozenset({"s3", "b2", "azure", "gs", "rest", "sftp", "rclone", "swift"})
_SCHEME = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]+):")
_SNAPSHOT_ID = r"^[0-9a-f]{64}$"
MIN_SHORT_ID = 8

Backend = Literal["local", "remote", "unknown"]


class CollectionFailed(Exception):
    """Fallo global de la recogida; el mensaje es un diagnóstico estructurado."""


class _Snapshot(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(pattern=_SNAPSHOT_ID)
    time: AwareDatetime


def instant(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def backend_kind(repository: str) -> Backend:
    match = _SCHEME.match(repository)
    if match is None or match.group(1) == "local":
        return "local"
    return "remote" if match.group(1) in REMOTE_SCHEMES else "unknown"


def _run(execute: Executor, args: tuple[str, ...], label: str):
    try:
        return execute(args)
    except ResticUnavailable as exc:
        raise CollectionFailed(f"restic no disponible ({exc.kind}) al ejecutar {label}") from None


def read_repository(execute: Executor) -> str | None:
    """Id del repositorio, o `None` si no existe (código 10); el resto es un fallo global."""
    result = _run(execute, CAT_CONFIG, "cat config")
    if result.returncode == REPOSITORY_MISSING:
        return None
    if result.returncode == WRONG_PASSWORD:
        raise CollectionFailed("contraseña del repositorio incorrecta (código 12)")
    if result.returncode != 0:
        raise CollectionFailed(f"restic cat config falló (código {result.returncode})")
    found = parse_repository_id(result.stdout)
    if found is None:
        raise CollectionFailed("restic cat config devolvió una configuración sin id válido")
    return found


def collect_repository(repository: str, repository_id: str | None) -> Section:
    return Section(
        status=CollectionStatus.SUCCESS,
        items=[
            {
                "exists": repository_id is not None,
                "repository_id": repository_id,
                "backend": backend_kind(repository),
            }
        ],
    )


def collect_snapshots(
    execute: Executor, records: WorkspaceRecords, current: str | None, repository: str
) -> Section:
    if current is None:
        return Section(
            status=CollectionStatus.SUCCESS,
            summary={"count": 0, "foreign_snapshots": [], "repository_exists": False},
        )
    try:
        result = execute(LIST_SNAPSHOTS)
    except ResticUnavailable as exc:
        return Section(status=CollectionStatus.ERROR, reason=f"restic no disponible ({exc.kind})")
    if result.returncode != 0:
        return Section(
            status=CollectionStatus.ERROR,
            reason=f"restic snapshots falló (código {result.returncode})",
        )
    try:
        raw = json.loads(result.stdout or "[]")
        snapshots = [_Snapshot.model_validate(item) for item in raw]
    except (ValueError, TypeError, ValidationError):
        return Section(status=CollectionStatus.ERROR, reason="respuesta con forma inesperada")
    completed = [
        run
        for run in records.runs
        if run.status == "completed" and run.snapshot_id and _run_bound(run, current)
    ]
    items = []
    for snapshot in sorted(snapshots, key=lambda s: s.time):
        cited = next((r for r in completed if _cites(r.snapshot_id or "", snapshot.id)), None)
        items.append(
            {
                "id": snapshot.id,
                "time": instant(snapshot.time),
                "run_id": cited.run_id if cited else None,
                "foreign": cited is None,
            }
        )
    return Section(
        status=CollectionStatus.SUCCESS,
        items=items,
        summary={
            "count": len(items),
            "tag": TAG,
            "foreign_snapshots": [item["id"] for item in items if item["foreign"]],
        },
    )


def _cites(cited: str, full_id: str) -> bool:
    return cited == full_id or (len(cited) >= MIN_SHORT_ID and full_id.startswith(cited))


def _run_bound(run: BackupRun, current: str | None) -> bool:
    """Atado solo por id: sin `repository_id` (o sin repositorio actual) no hay prueba."""
    return run.repository_id is not None and run.repository_id == current


def _same_repository_string(run: BackupRun, repository: str) -> bool:
    return run.repository == repository


def _binding(record_repository_id: str | None, current: str | None) -> dict[str, Any]:
    return {
        "repository_id": record_repository_id,
        "bound": current is not None and record_repository_id == current,
    }


def _times(record: Any) -> dict[str, Any]:
    finished = getattr(record, "finished_at", None)
    return {
        "started_at": instant(record.started_at),
        "finished_at": instant(finished) if finished else None,
    }


def _listed(items: list[dict[str, Any]], unreadable: list[str]) -> Section:
    return Section(
        status=CollectionStatus.SUCCESS,
        items=items,
        summary={"count": len(items), "unreadable": sorted(unreadable)},
    )


def collect_runs(records: WorkspaceRecords, current: str | None, repository: str) -> Section:
    items = [
        {
            "run_id": run.run_id,
            "status": run.status,
            **_times(run),
            "snapshot_id": run.snapshot_id,
            "has_error": run.error is not None,
            "reconstructed_from_snapshot": run.reconstructed_from_snapshot,
            "repository_id": run.repository_id,
            "bound": _run_bound(run, current),
        }
        for run in records.runs
    ]
    section = _listed(items, records.unreadable.get("runs", []))
    section.summary["unbound_with_matching_repository_string"] = sorted(
        run.run_id
        for run in records.runs
        if not _run_bound(run, current) and _same_repository_string(run, repository)
    )
    return section


def collect_integrity_checks(records: WorkspaceRecords, current: str | None) -> Section:
    items = [
        {
            "check_id": check.check_id,
            "status": check.status,
            **_times(check),
            "scope": check.scope,
            "read_data_percent": check.read_data_percent,
            "exit_code": check.exit_code,
            "num_errors": check.num_errors,
            "broken_packs": check.broken_packs,
            "failure": check.failure,
            **_binding(check.repository_id, current),
        }
        for check in records.checks
    ]
    return _listed(items, records.unreadable.get("checks", []))


def collect_restores(records: WorkspaceRecords, current: str | None) -> Section:
    items = [
        {
            "restore_id": restore.restore_id,
            "status": restore.status,
            **_times(restore),
            "snapshot_id": restore.snapshot_id,
            "inventory_verified": restore.inventory_verified,
            "mismatches": restore.mismatches,
            "failure": restore.failure,
            **_binding(restore.repository_id, current),
        }
        for restore in records.restores
    ]
    return _listed(items, records.unreadable.get("restores", []))


def collect_restore_tests(
    records: WorkspaceRecords, current: str | None, snapshots: Section
) -> Section:
    """`restore_linked`: la restauración citada existe, está completada y verificada, solo la
    cita este test, `performed_at` cumple la regla del registro y su snapshot está en el
    repositorio actual (`snapshot_in_repository`, `None` si no se pudo comprobar), además de
    tener evidencia manual propia y un `system` inventariado. Si no, `restore_link_problem`
    dice por qué."""
    restores = {r.restore_id: r for r in records.restores}
    citations = Counter(t.restore_id for t in records.restore_tests)
    collected = snapshots.status is CollectionStatus.SUCCESS
    in_repository = [i["id"] for i in snapshots.items] if collected else []
    no_evidence = set(records.without_manual_evidence)
    not_inventoried = set(records.without_inventoried_system)
    items = []
    for test in sorted(records.restore_tests, key=lambda t: t.performed_at):
        cited = restores.get(test.restore_id)
        present = (
            any(_cites(test.backup.snapshot_id, full) for full in in_repository)
            if collected
            else None
        )
        manual_evidence = test.test_id not in no_evidence
        system_inventoried = test.test_id not in not_inventoried
        problem = _link_problem(
            test,
            cited,
            citations[test.restore_id],
            present,
            manual_evidence=manual_evidence,
            system_inventoried=system_inventoried,
        )
        items.append(
            {
                **test.model_dump(mode="json"),
                "performed_at": instant(test.performed_at),
                "review": {
                    **test.review.model_dump(mode="json"),
                    "reviewed_at": instant(test.review.reviewed_at),
                    "valid_until": instant(test.review.valid_until),
                },
                "manual_evidence": manual_evidence,
                "system_inventoried": system_inventoried,
                "snapshot_in_repository": present,
                "restore_linked": problem is None,
                "restore_link_problem": problem,
                **_binding(test.repository_id, current),
            }
        )
    return _listed(items, records.unreadable.get("restore_tests", []))


def _link_problem(
    test: RestoreTest,
    cited: RestoreRecord | None,
    citations: int,
    snapshot_present: bool | None,
    *,
    manual_evidence: bool,
    system_inventoried: bool,
) -> str | None:
    """Las reglas de `backup restore-test record`, reaplicadas a un registro editable a mano."""
    if cited is None or cited.status != "completed":
        return "la restauración citada no existe o no está completada"
    if cited.snapshot_id != test.backup.snapshot_id or cited.repository_id != test.repository_id:
        return "la restauración citada es de otro snapshot o repositorio"
    if cited.inventory_verified is not True:
        return "la restauración citada no verificó su inventario"
    if citations != 1:
        return "la restauración citada la cita más de un restore test"
    if problem := performed_after_restore_problem(cited.finished_at, test.performed_at):
        return problem
    if not manual_evidence:
        return "el restore test no tiene su evidencia manual (se colocó sin `restore-test record`)"
    if not system_inventoried:
        return "el system del restore test no es un activo del inventario"
    if snapshot_present is None:
        return "no se pudo comprobar el snapshot en el repositorio (sección snapshots no recogida)"
    if not snapshot_present:
        return "el snapshot del restore test no está en el repositorio"
    return None


def collect_immutability(records: WorkspaceRecords, repository: str) -> Section:
    """Estos registros guardan la cadena del repositorio, no su id: `bound` la compara."""
    items = [
        {
            "checked_at": instant(check.checked_at),
            "status": check.status,
            "results": [r.model_dump(mode="json") for r in check.results],
            "repository_id": None,
            "bound": check.repository == repository,
        }
        for check in sorted(records.immutability, key=lambda c: c.checked_at)
    ]
    return _listed(items, records.unreadable.get("immutability", []))
