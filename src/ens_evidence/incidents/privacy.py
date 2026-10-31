"""Minimización de datos del registro de incidentes.

Lista cerrada de campos admitidos; se valida toda la estructura (también la anidada) antes de
escribir nada. Los mensajes nombran el campo, nunca el valor.
"""

import ipaddress
import re
from typing import Any

from ens_evidence.incidents.model import OPAQUE_ID_PATTERN, TITLE_MAX
from ens_evidence.scope.secrets import find_secrets

_TEXT = "text"  # texto libre: se busca de todo
_ID = "id"  # valor estructurado (id, hash, instante, enum): solo secretos
_OPAQUE_LIST = ("opaque",)  # lista de ids opacos con formato fijo

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
        "source": {"system": _TEXT, "alert_ids": _OPAQUE_LIST},
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
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_DNI_NIE = re.compile(r"(?<![\w])(?:\d{8}|[XYZ]\d{7})-?[A-Z](?![\w])", re.IGNORECASE)
_IBAN = re.compile(r"(?<![\w])[A-Z]{2}\d{2}(?:[ -]?[A-Z0-9]){11,30}(?![\w])")
_PHONE_ES = re.compile(
    r"(?<![\w.:/-])(?:(?:\+|00)\d{1,3}[ .-]?)?[6-9]\d{2}(?:[ .-]?\d{2,3}){2,3}(?![\w])"
)
_PHONE_INTL = re.compile(r"(?<![\w.:/-])(?:\+|00)\d{1,3}(?:[ .-]?\d){7,12}(?![\w])")
_IPV4 = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
_IPV6 = re.compile(r"(?<![\w:])[0-9A-Fa-f]{0,4}(?::[0-9A-Fa-f]{0,4}){2,7}(?![\w:])")


def _valid_iban(candidate: str) -> bool:
    digits = re.sub(r"[ -]", "", candidate)
    rearranged = digits[4:] + digits[:4]
    number = "".join(str(int(c, 36)) for c in rearranged)
    return 15 <= len(digits) <= 34 and int(number) % 97 == 1


def _is_ip(candidate: str) -> bool:
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return False
    return True


def _identifiers(text: str) -> list[str]:
    found = []
    if _EMAIL.search(text):
        found.append("un correo electrónico")
    if _DNI_NIE.search(text):
        found.append("un DNI/NIE")
    if any(_valid_iban(m.group()) for m in _IBAN.finditer(text)):
        found.append("un IBAN")
    if _PHONE_ES.search(text) or _PHONE_INTL.search(text):
        found.append("un teléfono")
    return found


def _has_ip(text: str) -> bool:
    return any(_is_ip(m.group()) for m in _IPV4.finditer(text)) or any(
        m.group().count(":") >= 2 and _is_ip(m.group()) for m in _IPV6.finditer(text)
    )


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
        elif shape == _OPAQUE_LIST:
            self._opaque(value, path)
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

    def _opaque(self, value: Any, path: str) -> None:
        if not isinstance(value, list):
            self.errors.append(f"{path}: se esperaba una lista")
            return
        for index, item in enumerate(value):
            where = f"{path}[{index}]"
            if not isinstance(item, str) or not _OPAQUE.match(item):
                self.errors.append(f"{where}: no es un id opaco (A-Z a-z 0-9 . _ : -, 1-128)")
            else:
                self._leaf(_TEXT, item, where)

    def _leaf(self, shape: str, value: Any, path: str) -> None:
        if isinstance(value, dict | list):
            self.errors.append(f"{path}: se esperaba un valor simple")
            return
        self.errors += [f"{p}: parece un secreto; quítalo" for p in find_secrets(value, path)]
        if shape != _TEXT or not isinstance(value, str):
            return
        self.errors += [f"{path}: contiene {what}; quítalo" for what in _identifiers(value)]
        if _has_ip(value):
            self.warnings.append(
                f"{path}: contiene una dirección IP, que puede ser un dato personal"
            )


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
    errors += [f"--by: contiene {what}; indica solo un nombre" for what in _identifiers(name)]
    return errors
