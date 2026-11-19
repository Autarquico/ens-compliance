"""Carga del audit pack versionado, verificando su manifiesto (spec pre-audit).

El pack se lee una sola vez: los bytes que se verifican son los que se parsean. Si el manifiesto
no cuadra con los ficheros (alterado, faltante o sobrante), el pack se rechaza entero.
"""

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from importlib import resources
from importlib.resources.abc import Traversable
from typing import Any

import yaml
from pydantic import ValidationError

from ens_evidence.models.contracts import Strict
from ens_evidence.models.preaudit import Classification
from ens_evidence.yaml_unique import safe_load_unique

CURRENT_PACK = resources.files("ens_evidence.audit_pack") / "v3"
MANIFEST = "manifest.json"
PACK_FILES = ("criteria.yaml", "system.md", "response.schema.json")


class PackError(ValueError):
    """El pack no es íntegro o no es válido; no se usa."""


class Source(Strict):
    document: str
    reference: str
    quote: str


class MeasureScope(StrEnum):
    """Sobre qué medidas trabaja un criterio."""

    APPLICABLE = "applicable"
    """Medidas aplicables: el hallazgo cita documentos y evidencias enlazados a la medida."""
    NOT_APPLICABLE_OR_COMPENSATED = "not_applicable_or_compensated"
    """Medidas no aplicables o compensadas: el hallazgo cita su justificación."""


class Criterion(Strict):
    id: str
    text: str
    compare: str
    measure_scope: MeasureScope
    allowed_proposals: list[Classification]
    source: Source


@dataclass(frozen=True)
class AuditPack:
    version: str
    sha256: str
    criteria: tuple[Criterion, ...]
    system_prompt: str
    response_schema: dict[str, Any]

    def criterion(self, criterion_id: str) -> Criterion | None:
        return next((c for c in self.criteria if c.id == criterion_id), None)


def load_pack(root: Traversable | None = None) -> AuditPack:
    """El pack en `root`; sin él, `CURRENT_PACK` en el momento de la llamada."""
    root = CURRENT_PACK if root is None else root
    manifest_bytes = _read(root, MANIFEST)
    manifest = _parse_manifest(manifest_bytes)
    contents = {name: _read(root, name) for name in PACK_FILES}
    for name, data in contents.items():
        if hashlib.sha256(data).hexdigest() != manifest["files"].get(name):
            raise PackError(f"{name}: sha256 distinto del manifiesto")
    if set(manifest["files"]) != set(PACK_FILES):
        raise PackError("el manifiesto no lista exactamente los ficheros del pack")
    unlisted = {e.name for e in root.iterdir() if e.is_file()} - {MANIFEST, *PACK_FILES}
    if unlisted:
        raise PackError(f"ficheros fuera del manifiesto: {sorted(unlisted)}")
    try:
        criteria = _parse_criteria(contents["criteria.yaml"])
        schema = json.loads(contents["response.schema.json"])
        system_prompt = contents["system.md"].decode("utf-8")
    except (ValueError, yaml.YAMLError, ValidationError, KeyError, TypeError) as error:
        raise PackError(f"contenido del pack inválido: {error}") from error
    return AuditPack(
        version=manifest["version"],
        sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        criteria=criteria,
        system_prompt=system_prompt,
        response_schema=schema,
    )


def _read(root: Traversable, name: str) -> bytes:
    try:
        return (root / name).read_bytes()
    except OSError as error:
        raise PackError(f"{name}: no se puede leer el pack") from error


def _parse_manifest(data: bytes) -> dict[str, Any]:
    try:
        manifest = json.loads(data)
    except ValueError as error:
        raise PackError("manifest.json ilegible") from error
    files = manifest.get("files") if isinstance(manifest, dict) else None
    version = manifest.get("version") if isinstance(manifest, dict) else None
    if not isinstance(version, str) or not isinstance(files, dict):
        raise PackError("manifest.json sin version o files")
    return {"version": version, "files": files}


def _parse_criteria(data: bytes) -> tuple[Criterion, ...]:
    raw = safe_load_unique(data.decode("utf-8"))
    criteria = tuple(Criterion.model_validate(item) for item in raw["criteria"])
    if not criteria or len({c.id for c in criteria}) != len(criteria):
        raise ValueError("criterios vacíos o con id repetido")
    return criteria
