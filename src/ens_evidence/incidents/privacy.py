"""Minimización de datos del registro de incidentes.

Lista cerrada de campos admitidos; se valida toda la estructura (también la anidada) antes de
escribir nada. Los mensajes nombran el campo, nunca el valor.
"""

import re
from typing import Any

from ens_evidence.incidents.model import OPAQUE_ID_PATTERN, TITLE_MAX
from ens_evidence.privacy_text import text_problems
from ens_evidence.scope.secrets import find_secrets

_TEXT = "text"  # texto libre: se busca de todo
_ID = "id"  # valor estructurado (id, hash, instante, enum): solo secretos
_OPAQUE_LIST = ("opaque",)  # lista de ids opacos con formato fijo
_ALERT_IDS = ("alert_ids",)  # ids opacos que además admiten ids de alerta de Wazuh

_Shape = Any
_RECORD: dict[str, _Shape] = {
    "schema_version": _ID,
    "seq": _ID,
    "kind": _ID,
    "recorded_at": _ID,
    "previous_sha256": _ID,
    "state": {
        "id": _ID,
        "simulated": _ID,
        "title": _TEXT,
        "status": _ID,
        "severity": _TEXT,
        "detected_at": _ID,
        "assets": _OPAQUE_LIST,
        "source": {"system": _TEXT, "alert_ids": _ALERT_IDS},
        "personal_data": _ID,
        "analysis": {"summary": _TEXT, "recommendations": [_TEXT]},
        "actions": [{"kind": _ID, "at": _ID, "description": _TEXT}],
        "evidence_ids": [_ID],
        "procedure": {
            "name": _TEXT,
            "document_sha256": _ID,
            "approved_at": _ID,
            "approval_key": _ID,
        },
        "closure": {
            "closed_at": _ID,
            "conclusion": _TEXT,
            "legal_relevance": _ID,
            "legal_advice": _TEXT,
            "preserved_evidence_ids": [_ID],
            "auditable_events_review": {"result": _ID, "detail": _TEXT},
        },
    },
}

_OPAQUE = re.compile(OPAQUE_ID_PATTERN)
_SAFE_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,40}$")
# Id de alerta de Wazuh: epoch con fracción. Solo se admite en `source.alert_ids`.
_WAZUH_ALERT_ID = re.compile(r"\d{10}\.\d{1,10}")


def _key_label(key: object) -> str:
    return key if isinstance(key, str) and _SAFE_KEY.match(key) else "<clave>"


class _Walk:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def node(self, shape: _Shape, value: Any, path: str) -> None:
        if value is None:
            return
        if isinstance(shape, dict):
            self._mapping(shape, value, path)
        elif isinstance(shape, list):
            self._items(shape[0], value, path)
        elif shape in (_OPAQUE_LIST, _ALERT_IDS):
            self._opaque(value, path, shape == _ALERT_IDS)
        else:
            self._leaf(shape, value, path)

    def _mapping(self, shape: dict, value: Any, path: str) -> None:
        if not isinstance(value, dict):
            self.errors.append(f"{path or 'registro'}: se esperaba un objeto")
            return
        for key, child in value.items():
            where = f"{path}.{_key_label(key)}" if path else _key_label(key)
            if key in shape:
                self.node(shape[key], child, f"{path}.{key}" if path else str(key))
            else:
                self.errors.append(f"{where}: campo no admitido")

    def _items(self, item_shape: _Shape, value: Any, path: str) -> None:
        if not isinstance(value, list):
            self.errors.append(f"{path}: se esperaba una lista")
            return
        for index, item in enumerate(value):
            self.node(item_shape, item, f"{path}[{index}]")

    def _opaque(self, value: Any, path: str, allow_alert_id: bool = False) -> None:
        if not isinstance(value, list):
            self.errors.append(f"{path}: se esperaba una lista")
            return
        for index, item in enumerate(value):
            where = f"{path}[{index}]"
            if not isinstance(item, str) or not _OPAQUE.match(item):
                self.errors.append(f"{where}: no es un id opaco (A-Z a-z 0-9 . _ : -, 1-128)")
            elif allow_alert_id and _WAZUH_ALERT_ID.fullmatch(item):
                self._leaf(_ID, item, where)
            else:
                self._leaf(_TEXT, item, where)

    def _leaf(self, shape: str, value: Any, path: str) -> None:
        if isinstance(value, dict | list):
            self.errors.append(f"{path}: se esperaba un valor simple")
            return
        self.errors += [f"{p}: parece un secreto; quítalo" for p in find_secrets(value, path)]
        if shape != _TEXT or not isinstance(value, str):
            return
        errors, warnings = text_problems(value)
        self.errors += [f"{path}: {problem}; quítalo" for problem in errors]
        self.warnings += [f"{path}: {warning}" for warning in warnings]


def check_incident_payload(payload: Any) -> tuple[list[str], list[str]]:
    """(errores, avisos) de un registro serializado. Sin errores, es seguro escribirlo."""
    walk = _Walk()
    walk.node(_RECORD, payload, "")
    return walk.errors, walk.warnings


def check_declared_name(name: str) -> list[str]:
    """Errores de `--by`: un nombre declarado no puede ser vacío, largo, un secreto ni un
    identificador personal (correo, DNI/NIE, IBAN, teléfono). Nunca incluye el valor."""
    if not name.strip():
        return ["--by: indica quién registra (nombre declarado)"]
    errors = (
        [f"--by: demasiado largo (máximo {TITLE_MAX} caracteres)"] if len(name) > TITLE_MAX else []
    )
    errors += [f"{p}: parece un secreto; quítalo" for p in find_secrets(name, "--by")]
    errors += [f"--by: {problem}; indica solo un nombre" for problem in text_problems(name)[0]]
    return errors
