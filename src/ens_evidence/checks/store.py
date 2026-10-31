"""Historial de solo adición de las ejecuciones de checks (ADR 0012).

Clave: `evidence/checks/<conector>/<seq:06d>-<instante>-<sha12>.json`, con el instante de fin de la
ejecución y la huella de sus bytes canónicos. Al leer, conector, número de orden, instante y huella
deben coincidir con el contenido, y `finished_at` no puede decrecer con `seq`. Un registro
ilegible, un nombre que no coincide, un duplicado, un hueco o un orden de instantes roto dejan el
conector con `check_history_unreadable:<clave>`.

Límite conocido: quitar los últimos registros (truncado final) no deja rastro dentro del
historial. `StoredCheckResults` lo acota (si hay evidencia posterior a lo que cita el último
intento, bloquea); borrar a la vez la ejecución y la evidencia solo se detecta contrastando con la
copia externa (`backup` + `evidence verify`).
"""

import hashlib
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from itertools import pairwise
from typing import Any

from pydantic import ValidationError

from ens_evidence.checks.model import StoredCheckRun
from ens_evidence.evidence.layout import CHECKS_PREFIX
from ens_evidence.matrix.store import instant_stamp
from ens_evidence.workspace import AlreadyExists, NotFound, NotText, WorkspaceStore

_NAME = re.compile(r"\d{6}-\d{8}T\d{12}Z-[0-9a-f]{12}\.json")
UNATTRIBUTED = ""


class CheckHistoryUnreadable(Exception):
    def __init__(self, connector: str, keys: tuple[str, ...]) -> None:
        super().__init__(f"historial de checks de {connector} ilegible: {', '.join(keys)}")
        self.connector = connector
        self.keys = keys


@dataclass(frozen=True)
class StoredRun:
    key: str
    run: StoredCheckRun


@dataclass(frozen=True)
class RunScan:
    """`runs`: registros legibles por conector, en orden de `seq`. `unreadable`: conector -> claves
    cuyo contenido no se puede dar por bueno (`UNATTRIBUTED` si ni el conector se identifica)."""

    runs: dict[str, tuple[StoredRun, ...]]
    unreadable: dict[str, tuple[str, ...]]

    def last(self, connector: str) -> StoredRun | None:
        found = self.runs.get(connector, ())
        return found[-1] if found else None


def connector_prefix(connector: str) -> str:
    return f"{CHECKS_PREFIX}/{connector}"


def canonical_bytes(run: StoredCheckRun) -> bytes:
    document = run.model_dump(mode="json")
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def run_key(run: StoredCheckRun, data: bytes | None = None) -> str:
    data = canonical_bytes(run) if data is None else data
    stamp = instant_stamp(run.finished_at)
    return (
        f"{connector_prefix(run.connector)}/{run.seq:06d}-{stamp}-"
        f"{hashlib.sha256(data).hexdigest()[:12]}.json"
    )


def outcome_digest(run: StoredCheckRun) -> str:
    """Lo que hace que dos intentos sean el mismo resultado: todo salvo `seq`, instantes,
    `reason` y `evaluated_at`. Una evidencia nueva (otra huella de manifiesto) lo cambia aunque el
    resultado sea igual."""
    results = sorted(
        (
            {
                "check_id": r.check_id,
                "asset_id": r.asset_id,
                "subject": r.subject,
                "status": r.status.value,
                "collection_status": r.collection_status.value,
                "reason_code": r.reason_code,
                "evidence_ids": sorted(r.evidence_ids),
            }
            for r in run.results
        ),
        key=lambda r: (r["check_id"], r["asset_id"], r["subject"] or ""),
    )
    document: dict[str, Any] = {
        "connector": run.connector,
        "engine_version": run.engine_version,
        "scope_fingerprint": run.scope_fingerprint,
        "config_sha256": run.config_sha256,
        "inputs": sorted((i.evidence_id, i.manifest_sha256) for i in run.inputs),
        "expected_subjects": {k: sorted(v) for k, v in sorted(run.expected_subjects.items())},
        "results": results,
        "errors": sorted(run.errors),
        "complete": run.complete,
    }
    text = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode()).hexdigest()


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    found = dict(pairs)
    if len(found) != len(pairs):
        raise ValueError("clave repetida")
    return found


def _read(store: WorkspaceStore, key: str) -> StoredRun | None:
    if _NAME.fullmatch(key.rsplit("/", 1)[-1]) is None:
        return None
    try:
        data = store.read_bytes(key)
        run = StoredCheckRun.model_validate(
            json.loads(data.decode("utf-8"), object_pairs_hook=_no_duplicate_keys)
        )
    except (NotFound, NotText, UnicodeDecodeError, ValueError, ValidationError):
        return None
    return StoredRun(key, run) if key == run_key(run, data) else None


def _directory_connector(key: str) -> str:
    parts = key.removeprefix(f"{CHECKS_PREFIX}/").split("/")
    return parts[0] if len(parts) == 2 and parts[0] else UNATTRIBUTED


def scan_runs(store: WorkspaceStore, connector: str | None = None) -> RunScan:
    """Lee el historial de un conector, o el de todos."""
    prefix = CHECKS_PREFIX if connector is None else connector_prefix(connector)
    found: dict[str, list[StoredRun]] = defaultdict(list)
    bad: dict[str, list[str]] = defaultdict(list)
    for key in store.list(prefix):
        owner = _directory_connector(key)
        stored = _read(store, key)
        if stored is None or stored.run.connector != owner:
            bad[owner].append(key)
            continue
        found[owner].append(stored)
    for owner, items in found.items():
        items.sort(key=lambda s: s.run.seq)
        seqs = [s.run.seq for s in items]
        if any(
            later.run.finished_at < earlier.run.finished_at for earlier, later in pairwise(items)
        ):
            bad[owner].append(f"{connector_prefix(owner)}/order")
        for s in items:
            if seqs.count(s.run.seq) > 1:
                bad[owner].append(s.key)
        if seqs != list(range(1, len(seqs) + 1)) and owner not in bad:
            bad[owner].append(f"{connector_prefix(owner)}/seq")
    return RunScan(
        {owner: tuple(items) for owner, items in found.items() if owner not in bad},
        {owner: tuple(keys) for owner, keys in bad.items()},
    )


def record_run(store: WorkspaceStore, run: StoredCheckRun, now: datetime) -> str | None:
    """Registra el intento como fichero nuevo; `run.seq` se ignora y lo asigna el almacén.

    Devuelve la clave escrita, o None si no se escribió nada (mismo `outcome_digest` que el último
    intento). Un historial ilegible no se amplía (`CheckHistoryUnreadable`); se rechaza un
    `finished_at` posterior a `now` o anterior al del último intento: `seq` y `finished_at` deben
    dar el mismo orden, o un FAIL posterior podría quedar "antes" de un PASS.
    """
    if run.finished_at > now:
        raise ValueError("finished_at posterior al instante actual")
    scan = scan_runs(store, run.connector)
    if keys := scan.unreadable.get(run.connector):
        raise CheckHistoryUnreadable(run.connector, keys)
    last = scan.last(run.connector)
    if last is not None and run.finished_at < last.run.finished_at:
        raise ValueError("finished_at anterior al del último intento")
    if last is not None and outcome_digest(last.run) == outcome_digest(run):
        return None
    numbered = run.model_copy(update={"seq": (last.run.seq if last else 0) + 1})
    data = canonical_bytes(numbered)
    key = run_key(numbered, data)
    try:
        store.create_bytes(key, data)
    except AlreadyExists:
        return None
    return key
