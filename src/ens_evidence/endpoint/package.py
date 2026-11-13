"""Lectura y validación del `.tar.gz` que produce `collect-endpoint.sh`.

Todo se valida en memoria antes de persistir nada: el tar es entrada no fiable.
"""

import hashlib
import io
import json
import re
import tarfile
import zlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, ValidationError

from ens_evidence.endpoint.pack import QueryPack, load_pack, pack_sha256
from ens_evidence.evidence.importer import EvidenceImportError

MAX_PACKAGE_BYTES = 32 * 1024 * 1024
MAX_RESULT_BYTES = 10 * 1024 * 1024
MAX_TEXT_BYTES = 64 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
TAR_OVERHEAD_BYTES = 1024 * 1024
MAX_ROWS = 100_000
MAX_COLUMNS = 64
MAX_VALUE_CHARS = 4096
CLOCK_SKEW = timedelta(minutes=5)

META = "meta.json"
SUMS = "SHA256SUMS"
_SUM_LINE = re.compile(r"^([0-9a-f]{64})  (\S+)$")


class PackageRejected(EvidenceImportError):
    """El paquete no es válido; no se importa nada."""


class QueryRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    exit_code: int
    permission_hint: bool
    unsupported_hint: bool = False


class Meta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    script_version: int
    pack_id: str
    pack_version: str
    pack_sha256: str
    osquery_version: str
    platform: Literal["darwin", "linux"]  # el script no recoge en Windows
    hostname: str
    collected_at: AwareDatetime
    queries: list[QueryRun]


@dataclass(frozen=True)
class Package:
    meta: Meta
    raw_results: dict[str, bytes]
    sha256: str


def read_package(data: bytes, *, now: datetime) -> Package:
    pack = load_pack()
    members = _read_members(data, _allowed_names(pack))
    _verify_sums(members)
    meta = _parse_meta(members[META])
    _verify_meta(meta, pack, now)
    raw_results = {
        name.removeprefix("results/").removesuffix(".json"): body
        for name, body in members.items()
        if name.startswith("results/")
    }
    if set(raw_results) != {run.id for run in meta.queries}:
        raise PackageRejected(
            "los resultados del paquete no coinciden con las consultas de meta.json"
        )
    return Package(meta, raw_results, hashlib.sha256(data).hexdigest())


def _allowed_names(pack: QueryPack) -> set[str]:
    return {META, SUMS, *(f"results/{query.id}.json" for query in pack.queries)}


def _read_members(data: bytes, allowed: set[str]) -> dict[str, bytes]:
    if len(data) > MAX_PACKAGE_BYTES:
        raise PackageRejected("el paquete supera el tamaño máximo permitido")
    members: dict[str, bytes] = {}
    declared = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(_gunzip(data)), mode="r:") as archive:
            for member in archive:
                if len(members) >= len(allowed):
                    raise PackageRejected("el paquete tiene demasiados miembros")
                limit = _limit(member.name)
                if member.name not in allowed or not member.isreg():
                    raise PackageRejected("el paquete contiene un miembro no permitido")
                if member.uname or member.gname or member.uid or member.gid:
                    raise PackageRejected(
                        "el paquete lleva propietario en las cabeceras del tar; "
                        "regenéralo con el script actual"
                    )
                if member.name in members:
                    raise PackageRejected("el paquete tiene un miembro duplicado")
                declared += member.size
                if member.size > limit or declared > MAX_TOTAL_BYTES:
                    raise PackageRejected("un miembro del paquete supera el tamaño permitido")
                body = archive.extractfile(member)
                members[member.name] = b"" if body is None else body.read(limit + 1)
    except (tarfile.TarError, EOFError, OSError, ValueError, zlib.error):
        raise PackageRejected("el fichero no es un .tar.gz legible") from None
    missing = {META, SUMS} - members.keys()
    if missing:
        raise PackageRejected(f"faltan miembros obligatorios: {', '.join(sorted(missing))}")
    return members


def _gunzip(data: bytes) -> bytes:
    """Descomprime con tope: tarfile lee enteras las cabeceras GNU/pax antes del miembro."""
    decompressor = zlib.decompressobj(wbits=31)
    limit = MAX_TOTAL_BYTES + TAR_OVERHEAD_BYTES
    raw = decompressor.decompress(data, limit + 1)
    if len(raw) > limit:
        raise PackageRejected("el paquete descomprimido supera el tamaño máximo permitido")
    if not decompressor.eof or decompressor.unused_data:
        raise PackageRejected("el fichero no es un .tar.gz legible")
    return raw


def _limit(name: str) -> int:
    return MAX_RESULT_BYTES if name.startswith("results/") else MAX_TEXT_BYTES


def _verify_sums(members: dict[str, bytes]) -> None:
    expected: dict[str, str] = {}
    try:
        lines = members[SUMS].decode("utf-8").splitlines()
    except UnicodeDecodeError:
        raise PackageRejected("SHA256SUMS no es texto UTF-8") from None
    for line in lines:
        found = _SUM_LINE.match(line)
        if found is None or found[2] in expected:
            raise PackageRejected("SHA256SUMS tiene una línea no válida o repetida")
        expected[found[2]] = found[1]
    if expected.keys() != members.keys() - {SUMS}:
        raise PackageRejected("SHA256SUMS no cubre exactamente los miembros del paquete")
    for name, digest in expected.items():
        if hashlib.sha256(members[name]).hexdigest() != digest:
            raise PackageRejected(f"SHA256SUMS no coincide con {name}")


def _parse_meta(body: bytes) -> Meta:
    try:
        return Meta.model_validate(json.loads(body))
    except (ValueError, RecursionError, ValidationError):
        raise PackageRejected("meta.json no es válido") from None


def _verify_meta(meta: Meta, pack: QueryPack, now: datetime) -> None:
    if meta.pack_version != str(pack.version) and meta.pack_version == "1":
        raise PackageRejected(
            "el paquete es de la versión 1 de consultas; repite la recogida con el script "
            "nuevo (`ens-evidence endpoint script`)"
        )
    if meta.pack_sha256 != pack_sha256() or (meta.pack_id, meta.pack_version) != (
        pack.id,
        str(pack.version),
    ):
        raise PackageRejected(
            "el paquete de consultas no es el de esta versión; genera el script con "
            "`ens-evidence endpoint script`"
        )
    expected = {query.id for query in pack.queries if meta.platform in query.platforms}
    if sorted(run.id for run in meta.queries) != sorted(expected):
        raise PackageRejected("meta.json no lista las consultas de su plataforma")
    if meta.collected_at > now.astimezone(UTC) + CLOCK_SKEW:
        raise PackageRejected("collected_at está en el futuro")


def parse_rows(body: bytes) -> list[dict[str, str]] | None:
    """Filas de una consulta, o `None` si el JSON es ilegible o excede los límites."""
    try:
        document: Any = json.loads(body)
    except (ValueError, RecursionError):
        return None
    if not isinstance(document, list) or len(document) > MAX_ROWS:
        return None
    for row in document:
        if not isinstance(row, dict) or len(row) > MAX_COLUMNS:
            return None
        if not all(
            isinstance(key, str) and isinstance(value, str) and len(value) <= MAX_VALUE_CHARS
            for key, value in row.items()
        ):
            return None
    return document
