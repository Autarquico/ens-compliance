import hashlib
import re
from importlib.resources import files
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, field_validator

PACK_DIR = files("ens_evidence.endpoint") / "queries" / "v3"
SCRIPT_NAME = "collect-endpoint.sh"
MANIFEST_NAME = "pack.manifest"
PACK_HASH_NAME = "pack.sha256"
QUERY_ID = re.compile(r"[a-z][a-z0-9_]*")


class Query(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    platforms: tuple[Literal["darwin", "linux", "windows"], ...]
    purpose: str
    verified: bool
    note: str

    @field_validator("id")
    @classmethod
    def safe_id(cls, value: str) -> str:
        if not QUERY_ID.fullmatch(value):
            raise ValueError(f"identificador de consulta no válido: {value!r}")
        return value


class QueryPack(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    version: int
    queries: tuple[Query, ...]


def load_pack() -> QueryPack:
    return QueryPack.model_validate(yaml.safe_load((PACK_DIR / "pack.yaml").read_text("utf-8")))


def pack_files() -> dict[str, bytes]:
    """Ficheros del paquete tal como se entregan al endpoint, por ruta relativa."""
    pack = load_pack()
    sql = {q.id: (PACK_DIR / f"{q.id}.sql").read_bytes() for q in pack.queries}
    listing = "".join(f"{q.id} {','.join(q.platforms)}\n" for q in pack.queries)
    return {
        "pack.yaml": (PACK_DIR / "pack.yaml").read_bytes(),
        "pack.info": f"id={pack.id}\nversion={pack.version}\n".encode(),
        "queries.list": listing.encode(),
        **{f"queries/{query_id}.sql": body for query_id, body in sql.items()},
    }


def manifest(contents: dict[str, bytes]) -> bytes:
    """Formato sha256sum, ordenado por ruta: su hash identifica el paquete."""
    lines = (
        f"{hashlib.sha256(body).hexdigest()}  {path}\n" for path, body in sorted(contents.items())
    )
    return "".join(lines).encode()


def pack_sha256() -> str:
    return hashlib.sha256(manifest(pack_files())).hexdigest()


def bundle_files() -> dict[str, bytes]:
    """Todo lo que `endpoint script` escribe en el directorio de salida."""
    contents = pack_files()
    listing = manifest(contents)
    return {
        SCRIPT_NAME: (files("ens_evidence.endpoint") / SCRIPT_NAME).read_bytes(),
        **contents,
        MANIFEST_NAME: listing,
        PACK_HASH_NAME: f"{hashlib.sha256(listing).hexdigest()}\n".encode(),
    }
