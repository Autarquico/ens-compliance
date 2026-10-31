"""Historial de solo adición de los cambios de la matriz, por medida.

`matrix.yaml` se sobrescribe: quitar un enlace y volver a ponerlo deja el mismo fichero. Cada
orden que lo modifica (`init`, `set`, `link`, `unlink`) registra aquí el estado resultante de la
entrada, y una revisión solo vale si desde su registro los enlaces no pasaron por otro estado.

Clave: `controls/matrix-history/<medida>/<seq>-<instante>-<sha12>.yaml`. Al leer, número de orden,
instante y huella de los bytes deben coincidir con el contenido; si no, el registro es ilegible
(su contenido real es desconocido). Una entrada de `matrix.yaml` que no coincide con el último
estado registrado se editó a mano: se bloquea la medida (ADR 0011).
"""

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime

import yaml
from pydantic import AwareDatetime, Field

from ens_evidence.approvals import load_model
from ens_evidence.matrix.fingerprint import entry_sha256, links_sha256
from ens_evidence.matrix.model import MatrixEntry, MatrixFile, MeasureId
from ens_evidence.matrix.store import instant_stamp
from ens_evidence.models.contracts import Strict
from ens_evidence.workspace import AlreadyExists, WorkspaceStore

HISTORY_PREFIX = "controls/matrix-history"
_NAME = re.compile(r"(\d{6})-(\d{8}T\d{12}Z)-([0-9a-f]{12})\.yaml")


class MatrixChange(Strict):
    """Estado de la entrada de una medida tras un cambio, y cuándo se registró."""

    measure_id: MeasureId
    seq: int = Field(ge=1, description="Número de orden dentro de la medida, desde 1.")
    recorded_at: AwareDatetime
    entry: MatrixEntry


@dataclass(frozen=True)
class ChangeScan:
    """`changes` en orden; `unreadable`: claves cuyo contenido no se puede dar por bueno."""

    changes: tuple[MatrixChange, ...]
    unreadable: tuple[str, ...]
    errors: tuple[str, ...]

    def last_entry(self, measure_id: str) -> MatrixEntry:
        """Último estado registrado; sin registros, la entrada vacía (`init` no escribe nada
        para una entrada sin contenido)."""
        return self.changes[-1].entry if self.changes else MatrixEntry(id=measure_id)

    def edited(self, entry: MatrixEntry) -> bool:
        return entry_sha256(entry) != entry_sha256(self.last_entry(entry.id))

    def links_changed_since(self, moment: datetime, links_sha: str) -> bool:
        """Algún registro posterior a `moment` dejó los enlaces o coberturas en otro estado."""
        return any(
            c.recorded_at > moment and links_sha256(c.entry) != links_sha for c in self.changes
        )


def history_prefix(measure_id: str) -> str:
    return f"{HISTORY_PREFIX}/{measure_id}"


def _key(change: MatrixChange, data: bytes) -> str:
    stamp = instant_stamp(change.recorded_at)
    return (
        f"{history_prefix(change.measure_id)}/{change.seq:06d}-{stamp}-"
        f"{hashlib.sha256(data).hexdigest()[:12]}.yaml"
    )


def _dump(change: MatrixChange) -> bytes:
    text = yaml.safe_dump(change.model_dump(mode="json"), sort_keys=False, allow_unicode=True)
    return text.encode("utf-8")


def scan_history(store: WorkspaceStore, measure_id: str) -> ChangeScan:
    found: list[MatrixChange] = []
    unreadable: list[str] = []
    errors: list[str] = []
    for key in store.list(history_prefix(measure_id)):
        change = load_model(store, key, MatrixChange, []) if _NAME.search(key) else None
        if (
            change is None
            or change.measure_id != measure_id
            or key != _key(change, store.read_bytes(key))
        ):
            unreadable.append(key)
            errors.append(f"{key}: registro de la matriz ilegible o que no coincide con su clave")
        else:
            found.append(change)
    found.sort(key=lambda c: c.seq)
    if [c.seq for c in found] != list(range(1, len(found) + 1)) and not unreadable:
        gap = f"{history_prefix(measure_id)}/seq"
        unreadable.append(gap)
        errors.append(f"{gap}: falta algún registro del historial de la matriz de {measure_id}")
    return ChangeScan(tuple(found), tuple(unreadable), tuple(errors))


def register_entry(store: WorkspaceStore, entry: MatrixEntry, now: datetime) -> bool:
    """Registra el estado de la entrada si difiere del último registrado; True si escribió."""
    scan = scan_history(store, entry.id)
    if not scan.edited(entry):
        return False
    seq = (scan.changes[-1].seq if scan.changes else 0) + 1
    change = MatrixChange(measure_id=entry.id, seq=seq, recorded_at=now, entry=entry)
    data = _dump(change)
    try:
        store.create_bytes(_key(change, data), data)
    except AlreadyExists:
        pass
    return True


def register_matrix(store: WorkspaceStore, matrix: MatrixFile, now: datetime) -> None:
    """Registra el estado de todas las entradas que difieren del último registrado."""
    for entry in matrix.entries:
        register_entry(store, entry, now)
