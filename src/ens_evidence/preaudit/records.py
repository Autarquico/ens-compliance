"""Las dos cadenas de solo adición de la pre-auditoría (`chain.py`), verificadas al leer.

- `evidence/preaudits/runs/`: las ejecuciones y sus hallazgos (`PreAuditRecord`);
- `evidence/preaudits/dispositions/`: la disposición humana de los hallazgos (`DispositionRecord`).

Una cadena con huecos, bifurcaciones, enlaces rotos, una clave que no corresponde a su contenido,
un `recorded_at` que retrocede o que está en el futuro respecto a `now` es un problema. Con
`known` se lee la cadena tal como era al generar un expediente: las claves congeladas incluyen los
registros y sus reservas; lo posterior no existía entonces y no se ve; algo congelado que falta sí
es un problema.

`append` añade un registro a una de las dos cadenas con el orden de escritura común: reservar la
secuencia (creación exclusiva) y después escribir.
"""

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel

from ens_evidence import chain
from ens_evidence.models.preaudit import DispositionRecord, PreAuditRecord
from ens_evidence.workspace import WorkspaceStore

PREAUDITS_PREFIX = "evidence/preaudits"
RUNS_PREFIX = f"{PREAUDITS_PREFIX}/runs"
DISPOSITIONS_PREFIX = f"{PREAUDITS_PREFIX}/dispositions"
RUNS_OWNER = "preaudits"


@dataclass(frozen=True)
class ChainScan[R: BaseModel]:
    records: tuple[chain.Stored[R], ...]
    problems: tuple[str, ...]


RunScan = ChainScan[PreAuditRecord]
DispositionScan = ChainScan[DispositionRecord]


def scan_runs(
    store: WorkspaceStore, *, now: datetime | None = None, known: Collection[str] | None = None
) -> RunScan:
    return _scan(store, RUNS_PREFIX, PreAuditRecord, "las pre-auditorías", now, known)


def scan_dispositions(
    store: WorkspaceStore, *, now: datetime | None = None, known: Collection[str] | None = None
) -> DispositionScan:
    return _scan(
        store, DISPOSITIONS_PREFIX, DispositionRecord, "las disposiciones de hallazgos", now, known
    )


def _scan[R: BaseModel](
    store: WorkspaceStore,
    prefix: str,
    model: type[R],
    label: str,
    now: datetime | None,
    known: Collection[str] | None,
) -> ChainScan[R]:
    view = store if known is None else _AsKnown(store, prefix, known)
    stored, reserved, problems = chain.read_chain(
        view,
        prefix,
        model,
        owner=RUNS_OWNER,
        owner_of=lambda _record: RUNS_OWNER,
        owner_label="registro",
        chain_label=label,
    )
    if known is not None:
        present = set(view.list(prefix))
        problems += [
            f"{key}: el registro que existía al generar el expediente ya no está"
            for key in sorted(set(known) - present)
        ]
    by_seq = chain.group_by_seq(stored)
    pairs = chain.linked_pairs(stored, by_seq)
    problems += chain.sequence_problems(by_seq, reserved)
    problems += chain.link_problems(pairs)
    problems += [
        f"registros {a.record.seq} y {b.record.seq}: recorded_at retrocede"
        for a, b in pairs
        if b.record.recorded_at < a.record.recorded_at
    ]
    if now is not None:
        problems += [
            f"{item.key}: recorded_at está en el futuro"
            for item in stored
            if item.record.recorded_at > now
        ]
    return ChainScan(tuple(stored), tuple(problems))


def next_link[R: (PreAuditRecord, DispositionRecord)](scan: ChainScan[R]) -> tuple[int, str | None]:
    """(`seq`, `previous_sha256`) del registro que sigue al último legible."""
    if not scan.records:
        return 1, None
    last = scan.records[-1]
    return last.record.seq + 1, last.sha256


def append[R: (PreAuditRecord, DispositionRecord)](
    store: WorkspaceStore, prefix: str, record: R
) -> chain.Stored[R]:
    """Reserva la secuencia de `record` y lo escribe; `chain.SequenceTaken` si otra orden la
    reservó antes (no se escribe nada). Cada llamador lo traduce a su rechazo."""
    data = chain.render_record(record.model_dump(mode="json"))
    chain.reserve_sequence(store, prefix, record.seq)
    key = chain.record_key(prefix, record.seq, record.recorded_at, data)
    store.create_bytes(key, data)
    return chain.Stored(key, data, record)


class _AsKnown:
    """La cadena con solo las claves de `known` (registros y reservas): lo que había al generar."""

    def __init__(self, store: WorkspaceStore, prefix: str, known: Collection[str]) -> None:
        self._store = store
        self._prefix = f"{prefix}/"
        self._visible = set(known)

    def _shown(self, key: str) -> bool:
        return not key.startswith(self._prefix) or key in self._visible

    def list(self, prefix: str = "") -> list[str]:
        return [key for key in self._store.list(prefix) if self._shown(key)]

    def read_bytes(self, key: str) -> bytes:
        return self._store.read_bytes(key)
