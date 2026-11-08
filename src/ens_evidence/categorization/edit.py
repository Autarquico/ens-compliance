"""Edición de `scope/categorization.yaml` sin tocar el YAML a mano.

Cada edición valida el fichero entero con `CategorizationFile`, conserva la cabecera de
comentarios de la plantilla y no aprueba nada. No hay registro de revocación: la aprobación
guarda el sha256 del fichero, así que cualquier cambio la deja STALE (categorization status).
"""

from datetime import datetime
from typing import Any, Literal

import yaml
from pydantic import ValidationError

from ens_evidence.catalog.model import Dimension
from ens_evidence.categorization.approval import CATEGORIZATION_KEY
from ens_evidence.categorization.models import CategorizationFile
from ens_evidence.models.enums import Role
from ens_evidence.scope.messages import describe_validation
from ens_evidence.scope.secrets import find_secrets
from ens_evidence.workspace import NotFound, WorkspaceStore
from ens_evidence.yaml_unique import safe_load_unique

Kind = Literal["information", "service"]
_LIST = {"information": "information", "service": "services"}
_ROLE = {"information": Role.INFORMATION_OWNER, "service": Role.SERVICE_OWNER}


class CategorizationEditRejected(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


def _split_header(text: str) -> tuple[str, str]:
    lines = text.splitlines(keepends=True)
    end = 0
    while end < len(lines) and (not lines[end].strip() or lines[end].startswith("#")):
        end += 1
    return "".join(lines[:end]), "".join(lines[end:])


def _read(store: WorkspaceStore) -> tuple[str, dict[str, Any]]:
    try:
        text = store.read_text(CATEGORIZATION_KEY)
    except NotFound:
        raise CategorizationEditRejected(
            [f"{CATEGORIZATION_KEY}: no existe; créalo con categorization init"]
        ) from None
    header, body = _split_header(text)
    try:
        raw = safe_load_unique(body)
    except yaml.YAMLError:
        raise CategorizationEditRejected(
            [f"{CATEGORIZATION_KEY}: no es YAML válido; revisa la sintaxis"]
        ) from None
    if not isinstance(raw, dict):
        raise CategorizationEditRejected([f"{CATEGORIZATION_KEY}: no tiene la forma esperada"])
    return header, raw


def _save(store: WorkspaceStore, header: str, document: dict[str, Any]) -> None:
    try:
        CategorizationFile.model_validate(document)
    except ValidationError as exc:
        raise CategorizationEditRejected(
            describe_validation(CATEGORIZATION_KEY, exc, document)
        ) from None
    body = yaml.safe_dump(document, sort_keys=False, allow_unicode=True, default_flow_style=False)
    store.write_text(CATEGORIZATION_KEY, header + body)


def _reject_secrets(fields: dict[str, Any]) -> None:
    if leaked := find_secrets(fields):
        raise CategorizationEditRejected(
            [f"{CATEGORIZATION_KEY}: {path} parece contener un secreto; quítalo" for path in leaked]
        )


def _element(document: dict[str, Any], kind: Kind, element_id: str) -> dict[str, Any]:
    for item in document.get(_LIST[kind]) or []:
        if isinstance(item, dict) and item.get("id") == element_id:
            return item
    raise CategorizationEditRejected(
        [f"{CATEGORIZATION_KEY}: no hay {kind} con id {element_id}; "
         "los servicios salen de categorization init"]
    )  # fmt: skip


def add_information(store: WorkspaceStore, info_id: str, description: str, now: datetime) -> None:
    """Añade una información sin valorar."""
    fields = {"id": info_id, "description": description, "ratings": {}}
    _reject_secrets(fields)
    header, document = _read(store)
    document["information"] = [*(document.get("information") or []), fields]
    _save(store, header, document)


def set_rating(
    store: WorkspaceStore,
    kind: Kind,
    element_id: str,
    dimension: Dimension,
    level: str,
    justification: str,
    now: datetime,
) -> None:
    """Fija el nivel (BAJO, MEDIO, ALTO o not_affected) de una dimensión, con su justificación."""
    fields = {"level": level, "justification": justification}
    _reject_secrets(fields)
    header, document = _read(store)
    element = _element(document, kind, element_id)
    ratings = element.get("ratings") or {}
    element["ratings"] = {**ratings, dimension.value: fields}
    _save(store, header, document)


def set_valuation(
    store: WorkspaceStore, kind: Kind, element_id: str, valued_by: str, now: datetime
) -> None:
    """Registra quién valora (declarado) y cuándo; el rol lo fija el tipo de elemento."""
    _reject_secrets({"valued_by": valued_by})
    header, document = _read(store)
    element = _element(document, kind, element_id)
    element["valuation"] = {
        "valued_by": valued_by,
        "role": _ROLE[kind].value,
        "valued_at": now.isoformat(),
    }
    _save(store, header, document)
