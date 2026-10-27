"""Exportar e importar el workspace completo, por bytes (ADR 0008)."""

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime

from ens_evidence.backup.records import (
    BackupRun,
    dump,
    read_snapshot_inventory,
    read_snapshot_marker,
    run_key,
)
from ens_evidence.evidence.layout import (
    BACKUP_META_PREFIX,
    BACKUPS_PREFIX,
    MANIFESTS_PREFIX,
    RAW_PREFIX,
    REVIEWS_PREFIX,
    sha256_hex,
)
from ens_evidence.scope import APPROVAL_KEY, revoke_approval
from ens_evidence.scope.sync import SYNCED_KEYS
from ens_evidence.workspace.errors import AlreadyExists, NotFound, WorkspaceError
from ens_evidence.workspace.store import WorkspaceStore

_WRITE_ONCE = (RAW_PREFIX, MANIFESTS_PREFIX, REVIEWS_PREFIX)


@dataclass(frozen=True)
class ImportReport:
    written: int
    unchanged: int
    approval_revoked: bool
    backup_note: str | None = None


def export_all(
    source: WorkspaceStore, target: WorkspaceStore, exclude: Collection[str] = ()
) -> list[str]:
    """Copia las claves de `source` (salvo `exclude`) a `target`; aborta si el listado cambia."""
    before = [key for key in source.list("") if key not in exclude]
    copied: dict[str, str] = {}
    for key in before:
        data = source.read_bytes(key)
        copied[key] = sha256_hex(data)
        target.write_bytes(key, data)
    if [k for k in source.list("") if k not in exclude] != before or _changed_keys(source, copied):
        raise WorkspaceError(
            "el workspace cambió durante la exportación; se aborta porque la copia sería parcial"
        )
    return before


def _changed_keys(source: WorkspaceStore, copied: dict[str, str]) -> list[str]:
    return [key for key, digest in copied.items() if sha256_hex(source.read_bytes(key)) != digest]


def import_all(destination: WorkspaceStore, source: WorkspaceStore, now: datetime) -> ImportReport:
    """Copia todas las claves de `source` a `destination`, sin pisar evidencias distintas.

    Si la carpeta viene de `backup restore`, antes de escribir nada se exige que sus claves y
    sha256 coincidan con el inventario del snapshot. Si el alcance cambia se revoca la
    aprobación del destino, igual que `workspace push`, y la
    aprobación de la copia solo se restaura si el destino no tiene ninguna.
    """
    contents = {key: source.read_bytes(key) for key in source.list("")}
    _require_snapshot_inventory(contents, source)
    contents, backup_note = _settle_backup_records(contents, source, now)
    keys = sorted(
        (key for key in contents if not _is_backup_meta(key)), key=lambda key: (_phase(key), key)
    )
    existing = {key: _read_if_present(destination, key) for key in keys}
    conflicts = [
        key for key in keys if _is_write_once(key) and existing[key] not in (None, contents[key])
    ]
    if conflicts:
        raise WorkspaceError(
            "no se importa nada: ya existen estas evidencias con contenido distinto: "
            + ", ".join(conflicts)
        )
    scope_changes = any(existing[key] != contents[key] for key in SYNCED_KEYS if key in contents)
    revoked = scope_changes and revoke_approval(destination, "workspace import", now)
    written = 0
    for key in keys:
        if _keep_destination(key, existing[key]) or existing[key] == contents[key]:
            continue
        _write(destination, key, contents[key])
        written += 1
    return ImportReport(written, len(keys) - written, revoked, backup_note)


def _require_snapshot_inventory(contents: dict[str, bytes], folder: WorkspaceStore) -> None:
    """Aborta, sin escrituras, si lo que se va a importar difiere del inventario del snapshot.

    La exportación nunca incluye el registro de la propia ejecución, así que su presencia en la
    carpeta es una clave añadida y también aborta.
    """
    marker = read_snapshot_marker(folder)
    meta = read_snapshot_inventory(folder)
    if marker is None or meta is None or meta.run_id != marker.run_id:
        return
    actual = {key: sha256_hex(data) for key, data in contents.items() if not _is_backup_meta(key)}
    expected = meta.inventory
    differing = [
        key for key in sorted(set(expected) | set(actual)) if expected.get(key) != actual.get(key)
    ]
    if differing:
        raise WorkspaceError(
            "no se importa nada: la carpeta difiere del inventario del snapshot "
            f"{meta.run_id} (modificadas, eliminadas o añadidas): " + ", ".join(differing)
        )


def _is_write_once(key: str) -> bool:
    return any(key.startswith(f"{prefix}/") for prefix in _WRITE_ONCE)


def _keep_destination(key: str, current: bytes | None) -> bool:
    """La aprobación y los registros de copia del destino mandan sobre los de la copia."""
    return current is not None and (key == APPROVAL_KEY or key.startswith(f"{BACKUPS_PREFIX}/"))


def _read_if_present(store: WorkspaceStore, key: str) -> bytes | None:
    try:
        return store.read_bytes(key)
    except NotFound:
        return None


def _write(store: WorkspaceStore, key: str, data: bytes) -> None:
    if not _is_write_once(key):
        store.write_bytes(key, data)
        return
    try:
        store.create_bytes(key, data)
    except AlreadyExists:
        if store.read_bytes(key) != data:
            raise WorkspaceError(
                f"la evidencia {key} apareció con contenido distinto durante la importación"
            ) from None


def _phase(key: str) -> int:
    """Artefactos antes que manifiestos y reseñas: un fallo solo puede dejar huérfanos."""
    for index, prefix in enumerate(_WRITE_ONCE):
        if key.startswith(f"{prefix}/"):
            return index
    return len(_WRITE_ONCE)


def _is_backup_meta(key: str) -> bool:
    return key.startswith(f"{BACKUP_META_PREFIX}/")


def _settle_backup_records(
    contents: dict[str, bytes], folder: WorkspaceStore, now: datetime
) -> tuple[dict[str, bytes], str | None]:
    """Una copia restaurada y verificada nunca deja su ejecución como interrumpida.

    Solo si `backup restore` verificó el snapshot (marcador) y la carpeta trae exactamente
    el inventario de esa ejecución, se reconstruye siempre como completed el registro de esa
    ejecución, que la exportación excluyó. Si no, no se reconstruye.
    """
    if not any(_is_backup_meta(key) for key in contents):
        return contents, (
            "Importación NO verificada contra una copia: la carpeta no viene de `backup restore` "
            "y no se reconstruye ningún registro de copia."
        )
    marker = read_snapshot_marker(folder)
    meta = read_snapshot_inventory(folder)
    if marker is None or meta is None or meta.run_id != marker.run_id:
        return contents, (
            "La carpeta no trae un snapshot verificado por `backup restore` con un único "
            "inventario que le corresponda: no se reconstruye ningún registro de copia."
        )
    base = BackupRun(
        run_id=meta.run_id,
        status="started",
        started_at=meta.started_at,
        repository=meta.repository,
    )
    settled = dict(contents)
    settled[run_key(meta.run_id)] = dump(
        BackupRun.model_validate(
            base.model_copy(
                update={
                    "status": "completed",
                    "finished_at": marker.snapshot_time or meta.exported_at,
                    "snapshot_id": marker.snapshot_id,
                    "inventory": meta.inventory,
                    "reconstructed_from_snapshot": True,
                    "reconstructed_at": now,
                }
            ).model_dump()
        )
    )
    return settled, f"Registro de la ejecución {meta.run_id} reconstruido desde el snapshot."
