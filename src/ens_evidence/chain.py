"""Cadena de registros de solo adición, neutral: la comparten incidentes y expediente.

Disposición bajo un prefijo del workspace:

- `<prefijo>/seq/<n>`: reserva de la secuencia, con creación exclusiva (quien la crea gana);
- `<prefijo>/<n>-<instante>-<sha12>.yaml`: el registro, cuya clave se verifica contra su contenido;
- cada registro cita la huella (`previous_sha256`) del fichero anterior.

Aquí solo hay lo genérico (nombres, lectura, secuencia, enlace, reserva). Cada dominio añade sus
propias reglas sobre los registros ya leídos.
"""

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import yaml
from pydantic import AwareDatetime, BaseModel, ValidationError

from ens_evidence.scope.messages import describe_validation
from ens_evidence.workspace import AlreadyExists, WorkspaceStore
from ens_evidence.yaml_unique import safe_load_unique

RECORD_NAME = re.compile(r"(\d{6})-(\d{8}T\d{12}Z)-([0-9a-f]{12})\.yaml")
RESERVATION_NAME = re.compile(r"seq/(\d{6})")
RESERVED = b"reservada\n"


class Chained(Protocol):
    @property
    def seq(self) -> int: ...
    @property
    def recorded_at(self) -> AwareDatetime: ...
    @property
    def previous_sha256(self) -> str | None: ...


class SequenceTaken(Exception):
    """Otra orden reservó antes esa secuencia."""


@dataclass(frozen=True)
class Stored[R: Chained]:
    key: str
    data: bytes
    record: R

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


def instant_stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")


def seq_key(prefix: str, seq: int) -> str:
    return f"{prefix}/seq/{seq:06d}"


def record_key(prefix: str, seq: int, recorded_at: datetime, data: bytes) -> str:
    sha12 = hashlib.sha256(data).hexdigest()[:12]
    return f"{prefix}/{seq:06d}-{instant_stamp(recorded_at)}-{sha12}.yaml"


def render_record(payload: dict[str, Any]) -> bytes:
    """YAML canónico (claves ordenadas) del registro serializado."""
    return yaml.safe_dump(payload, sort_keys=True, allow_unicode=True).encode("utf-8")


def reserve_sequence(store: WorkspaceStore, prefix: str, seq: int) -> None:
    """Reserva con creación exclusiva; `SequenceTaken` si otra orden ganó."""
    try:
        store.create_bytes(seq_key(prefix, seq), RESERVED)
    except AlreadyExists:
        raise SequenceTaken(seq) from None


def read_chain[R: BaseModel](
    store: WorkspaceStore,
    prefix: str,
    model: type[R],
    *,
    owner: str,
    owner_of: Callable[[R], str],
    owner_label: str,
    chain_label: str,
) -> tuple[list[Stored[R]], set[int], list[str]]:
    """Registros legibles (ordenados por número), reservas y problemas de lectura.

    `owner_label` y `chain_label` solo alimentan los mensajes («otro incidente», «en el registro
    de incidentes»)."""
    problems: list[str] = []
    records: list[Stored[R]] = []
    reserved: set[int] = set()
    for key in store.list(prefix):
        name = key.removeprefix(f"{prefix}/")
        if (match := RESERVATION_NAME.fullmatch(name)) is not None:
            reserved.add(int(match.group(1)))
        elif RECORD_NAME.fullmatch(name) is not None:
            stored = _read_record(store, prefix, key, model, owner, owner_of, owner_label, problems)
            if stored is not None:
                records.append(stored)
        else:
            problems.append(f"{key}: clave inesperada en {chain_label}")
    records.sort(key=lambda s: (s.record.seq, s.key))
    return records, reserved, problems


def _read_record[R: BaseModel](
    store: WorkspaceStore,
    prefix: str,
    key: str,
    model: type[R],
    owner: str,
    owner_of: Callable[[R], str],
    owner_label: str,
    problems: list[str],
) -> Stored[R] | None:
    raw = None
    try:
        data = store.read_bytes(key)
        raw = safe_load_unique(data.decode("utf-8"))
        record = model.model_validate(raw)
    except UnicodeDecodeError:
        problems.append(f"{key}: no es texto UTF-8")
    except yaml.YAMLError:
        problems.append(f"{key}: YAML no válido o con una clave repetida")
    except ValidationError as exc:
        problems += describe_validation(key, exc, raw)
    else:
        if owner_of(record) != owner:
            problems.append(f"{key}: el registro es de otro {owner_label}")
            return None
        if key != record_key(prefix, record.seq, record.recorded_at, data):  # type: ignore[attr-defined]
            problems.append(
                f"{key}: la clave no coincide con el contenido (número, instante o hash)"
            )
        return Stored(key, data, record)
    return None


def group_by_seq[R: Chained](records: list[Stored[R]]) -> dict[int, list[Stored[R]]]:
    by_seq: dict[int, list[Stored[R]]] = {}
    for stored in records:
        by_seq.setdefault(stored.record.seq, []).append(stored)
    return by_seq


def sequence_problems[R: Chained](
    by_seq: dict[int, list[Stored[R]]], reserved: set[int]
) -> list[str]:
    """Bifurcaciones, huecos y correspondencia exacta entre reservas y registros."""
    problems = [
        f"bifurcación: {len(group)} registros con el mismo número {seq}: "
        + ", ".join(s.key for s in group)
        for seq, group in sorted(by_seq.items())
        if len(group) > 1
    ]
    last = max(by_seq, default=0)
    problems += [f"falta el registro {seq}" for seq in range(1, last + 1) if seq not in by_seq]
    problems += [
        f"reserva de la secuencia {seq} sin registro" for seq in sorted(reserved - set(by_seq))
    ]
    problems += [
        f"registro {seq} sin reserva de la secuencia" for seq in sorted(set(by_seq) - reserved)
    ]
    return problems


def linked_pairs[R: Chained](
    records: list[Stored[R]], by_seq: dict[int, list[Stored[R]]]
) -> list[tuple[Stored[R], Stored[R]]]:
    """Pares contiguos sin bifurcación: sobre ellos tiene sentido comprobar el enlace."""
    linear = [s for s in records if len(by_seq[s.record.seq]) == 1]
    return [
        (before, after)
        for before, after in zip(linear, linear[1:], strict=False)
        if after.record.seq == before.record.seq + 1
    ]


def link_problems[R: Chained](pairs: list[tuple[Stored[R], Stored[R]]]) -> list[str]:
    return [
        f"registros {before.record.seq} y {after.record.seq}: la cadena previous_sha256 está rota"
        for before, after in pairs
        if after.record.previous_sha256 != before.sha256
    ]
