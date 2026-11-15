"""Prueba de restauración hecha fuera de `backup restore` (p. ej. con Backrest, kit de copias).

La herramienta no restaura: compara la carpeta restaurada con la original, fichero a fichero
(sha256), y solo si coinciden registra la restauración como verificada y la prueba que la cita.
Quien la declara (`review.reviewer`) es siempre una persona; nada se rellena por ella.
"""

import hashlib
import os
import secrets
import stat
from datetime import datetime
from pathlib import Path

import yaml

from ens_evidence.backup.errors import BackupError
from ens_evidence.backup.records import RestoreRecord, dump, restore_key
from ens_evidence.backup.restore_test import (
    RestoreTest,
    RestoreTestDeclaration,
    record_declared,
    require_inventoried_system,
)
from ens_evidence.workspace import AlreadyExists, WorkspaceStore
from ens_evidence.yaml_unique import safe_load_unique

MAX_LISTED_MISMATCHES = 20
_CHUNK = 1024 * 1024


def _unreadable(error: OSError) -> None:
    raise BackupError(f"no se puede recorrer {error.filename}: {error.strerror}")


def inventory_of_tree(root: Path) -> dict[str, str]:
    """`{ruta relativa: sha256}` de los ficheros normales; lo que no lo es consta como tal.

    Un enlace a directorio consta como `no-regular` (no se sigue) y una carpeta que no se
    puede leer para el inventario: callarla daría por buena una restauración incompleta.
    """
    found: dict[str, str] = {}
    for directory, dirs, names in os.walk(root, followlinks=False, onerror=_unreadable):
        for name in dirs:
            path = Path(directory) / name
            if path.is_symlink():
                found[path.relative_to(root).as_posix()] = "no-regular"
        for name in names:
            path = Path(directory) / name
            key = path.relative_to(root).as_posix()
            if not stat.S_ISREG(path.lstat().st_mode):
                found[key] = "no-regular"
                continue
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                while chunk := handle.read(_CHUNK):
                    digest.update(chunk)
            found[key] = digest.hexdigest()
    return found


def differences(restored: dict[str, str], original: dict[str, str]) -> list[str]:
    """Claves que faltan, sobran o cambian entre lo restaurado y el original."""
    missing = [f"falta en lo restaurado: {k}" for k in sorted(original.keys() - restored.keys())]
    extra = [f"sobra en lo restaurado: {k}" for k in sorted(restored.keys() - original.keys())]
    changed = [
        f"distinto contenido: {k}"
        for k in sorted(restored.keys() & original.keys())
        if restored[k] != original[k] or restored[k] == "no-regular"
    ]
    return [*missing, *extra, *changed]


def _require_separate_folders(restored: Path, original: Path) -> None:
    for folder in (restored, original):
        if not folder.is_dir():
            raise BackupError(f"{folder} no es una carpeta")
    a, b = restored.resolve(), original.resolve()
    if a == b or a in b.parents or b in a.parents:
        raise BackupError(
            "la carpeta restaurada y la original deben ser distintas y no estar una dentro de la "
            "otra: comparar una carpeta consigo misma no verifica nada"
        )


def _declaration(data: bytes, restore_id: str) -> RestoreTestDeclaration:
    try:
        loaded = safe_load_unique(data)
    except yaml.YAMLError:
        raise BackupError("el fichero no es YAML válido") from None
    if isinstance(loaded, dict) and "restore_id" in loaded:
        raise BackupError(
            "la declaración no lleva restore_id: lo genera esta orden al verificar la carpeta"
        )
    if not isinstance(loaded, dict):
        return RestoreTestDeclaration.model_validate(loaded)
    return RestoreTestDeclaration.model_validate({**loaded, "restore_id": restore_id})


def record_verified_restore_test(
    store: WorkspaceStore,
    repository_id: str,
    data: bytes,
    restored: Path,
    original: Path,
    now: datetime,
) -> RestoreTest:
    """Verifica `restored` contra `original`, registra la restauración y la prueba que la cita.

    `performed_at` de la declaración es cuándo se hizo la restauración (la declara la persona).
    Si hay diferencias, la restauración queda registrada como NO verificada, no se crea la
    prueba y se lanza `BackupError`; sin carpeta comparable tampoco se registra nada.
    """
    restore_id = f"kit-{now:%Y%m%dT%H%M%SZ}-{secrets.token_hex(3)}"
    declaration = _declaration(data, restore_id)
    if declaration.performed_at > now:
        raise BackupError("performed_at está en el futuro")
    if declaration.review.reviewed_at > now:
        raise BackupError("review.reviewed_at está en el futuro")
    require_inventoried_system(store, declaration.system)
    _require_separate_folders(restored, original)
    restored_inventory = inventory_of_tree(restored)
    original_inventory = inventory_of_tree(original)
    if not original_inventory:
        raise BackupError("la carpeta original no tiene ficheros: no hay nada que comparar")
    problems = differences(restored_inventory, original_inventory)
    _write_restore(
        store, repository_id, declaration, restored, verified=not problems, count=len(problems)
    )
    if problems:
        shown = problems[:MAX_LISTED_MISMATCHES]
        more = len(problems) - len(shown)
        raise BackupError(
            "lo restaurado no coincide con el original ("
            f"{len(problems)} diferencias); la restauración queda registrada como NO verificada "
            "y no se crea la prueba:\n  "
            + "\n  ".join(shown)
            + (f"\n  … y {more} más" if more else "")
        )
    return record_declared(store, repository_id, declaration, now)


def _write_restore(
    store: WorkspaceStore,
    repository_id: str,
    declaration: RestoreTestDeclaration,
    restored: Path,
    *,
    verified: bool,
    count: int,
) -> None:
    record = RestoreRecord(
        restore_id=declaration.restore_id,
        status="completed",
        started_at=declaration.performed_at,
        finished_at=declaration.performed_at,
        repository_id=repository_id,
        target=str(restored),
        snapshot_id=declaration.backup.snapshot_id,
        inventory_verified=verified,
        mismatches=count,
    )
    try:
        store.create_bytes(restore_key(record.restore_id), dump(record))
    except AlreadyExists:
        raise BackupError(
            "ya hay una restauración registrada con ese id; repite la orden"
        ) from None
