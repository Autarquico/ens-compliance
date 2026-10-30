"""Primitivas compartidas por las aprobaciones (categorización, riesgos, Declaración)."""

import hashlib
from collections.abc import Iterable
from datetime import datetime
from typing import Annotated, Any

import yaml
from pydantic import AfterValidator, BaseModel, ValidationError

from ens_evidence.models.enums import Role
from ens_evidence.scope.messages import SEPARATOR, describe_validation
from ens_evidence.scope.secrets import find_secrets
from ens_evidence.workspace import NotFound, NotText, WorkspaceStore
from ens_evidence.yaml_unique import safe_load_unique


class Rejected(Exception):
    """Una aprobación o firma que no se concede; `errors` dice por qué."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


def _only_security_officer(value: Role) -> Role:
    if value is not Role.SECURITY_OFFICER:
        raise ValueError("solo el responsable de la seguridad (security_officer) puede firmar")
    return value


SecurityOfficer = Annotated[Role, AfterValidator(_only_security_officer)]


def one_year_after(moment: datetime) -> datetime:
    """Mismo día y mes del año siguiente; el 29 de febrero vence el 28 de febrero."""
    try:
        return moment.replace(year=moment.year + 1)
    except ValueError:
        return moment.replace(year=moment.year + 1, day=28)


def is_current(approved_at: datetime, valid_until: datetime, now: datetime) -> bool:
    """Vigente en [approved_at, valid_until): una fecha de aprobación futura no vale."""
    return approved_at <= now < valid_until


def file_sha256(store: WorkspaceStore, key: str) -> str:
    """Huella de los bytes del fichero; lanza `NotFound` si no existe."""
    return hashlib.sha256(store.read_bytes(key)).hexdigest()


def file_hashes(store: WorkspaceStore, keys: Iterable[str]) -> dict[str, str]:
    """Huella por clave de los ficheros que existen; los ausentes no figuran."""
    hashes = {}
    for key in keys:
        try:
            hashes[key] = file_sha256(store, key)
        except NotFound:
            continue
    return hashes


def read_record[M: BaseModel](
    store: WorkspaceStore, key: str, model: type[M], redo: str
) -> tuple[M | None, list[str]]:
    """Lee un registro de aprobación: (None, []) si no existe; (None, errores) si no se lee.

    `redo` es la acción que repara el registro ("vuelve a aprobar").
    """
    try:
        raw: Any = safe_load_unique(store.read_text(key))
        return model.model_validate(raw), []
    except NotFound:
        return None, []
    except NotText:
        return None, [f"{key}: no es texto UTF-8; {redo}"]
    except yaml.YAMLError:
        return None, [f"{key}: no es YAML válido o repite una clave; {redo}"]
    except ValidationError as exc:
        return None, describe_validation(key, exc)


def load_model[M: BaseModel](
    store: WorkspaceStore, key: str, model: type[M], errors: list[str]
) -> M | None:
    """Lee un fichero YAML editado a mano y lo valida; los problemas se añaden a `errors`."""
    try:
        text = store.read_text(key)
    except NotFound:
        errors.append(f"{key}: no existe; créalo con ens-evidence risk init")
        return None
    except NotText:
        errors.append(f"{key}: no es texto UTF-8; guárdalo como UTF-8")
        return None
    try:
        raw: Any = safe_load_unique(text)
    except yaml.YAMLError:
        errors.append(f"{key}: no es YAML válido o repite una clave; revisa la sintaxis")
        return None
    if leaked := find_secrets(raw):
        errors.extend(
            f"{key}{SEPARATOR}{path}: parece un secreto; quítalo del fichero" for path in leaked
        )
        return None
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        errors.extend(describe_validation(key, exc, raw))
        return None
