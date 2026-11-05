"""Acceso a la partición reservada: una sola puerta para todas las órdenes (plan, crítica 9).

Mirar la reservada exige `--measurement` y deja una línea en `measurements.jsonl`, de solo
adición. Eso no impide mirarla por otros medios: hace visibles las miradas hechas con la
herramienta.
"""

from datetime import datetime
from pathlib import Path

from ens_evidence.benchmark.model import Measurement, Partition
from ens_evidence.models.preaudit import Provider

MEASUREMENTS_FILE = "measurements.jsonl"


class HoldoutLocked(Exception):
    """Se pidió la reservada sin `--measurement`."""

    def __init__(self, action: str) -> None:
        super().__init__(
            f"{action}: la partición reservada solo se usa para medir; añade --measurement "
            f"(queda anotado en {MEASUREMENTS_FILE})"
        )


def open_partition(
    root: Path,
    partition: Partition,
    *,
    action: str,
    measurement: bool,
    now: datetime,
    pack_sha256: str | None = None,
    provider: Provider | None = None,
    model: str | None = None,
) -> Measurement | None:
    """Deja pasar la de desarrollo sin más. La reservada, solo con `measurement` y tras anotar
    la mirada; lanza `HoldoutLocked` si falta. Devuelve la línea anotada."""
    if partition is Partition.DEV:
        return None
    if not measurement:
        raise HoldoutLocked(action)
    entry = Measurement(
        at=now, action=action, pack_sha256=pack_sha256, provider=provider, model=model
    )
    append_measurement(root, entry)
    return entry


def append_measurement(root: Path, entry: Measurement) -> None:
    """Añade una línea a `measurements.jsonl`; nunca reescribe las anteriores."""
    with (root / MEASUREMENTS_FILE).open("a", encoding="utf-8") as ledger:
        ledger.write(entry.model_dump_json(exclude_none=True) + "\n")
