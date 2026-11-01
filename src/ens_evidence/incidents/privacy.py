"""Minimización de datos del registro de incidentes.

Lista cerrada de campos admitidos; se valida toda la estructura (también la anidada) antes de
escribir nada. Los mensajes nombran el campo, nunca el valor.
"""

import ipaddress
import re
import string
from typing import Any

from ens_evidence.incidents.model import OPAQUE_ID_PATTERN, TITLE_MAX
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
_EMAIL = re.compile(r"[\w.+-]+\s?@\s?[\w-]+(?:\.[\w-]+)+")
# DNI/NIE: dígitos con separadores opcionales y letra final. Si la letra va separada, debe ser la
# letra de control válida («20261015 a las» no es un DNI); pegada, cualquier letra.
_DNI_NIE = re.compile(
    r"(?<![A-Za-z0-9])(?P<body>(?:\d(?:[ .-]?\d){7}|[XYZ](?:[ .-]?\d){7}))"
    r"(?P<sep>[ .-]?)(?P<letter>[A-Z])(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_CONTROL_LETTERS = "TRWAGMYFPDXBNJZSQVHLCKE"
_IBAN_START = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{2}\d{2}")
_IBAN_SEPARATORS = " .-_/"
_ALNUM = frozenset(string.ascii_letters + string.digits)
# Ids propios del registro: no son teléfonos aunque lleven muchos dígitos.
_OWN_IDS = re.compile(r"(?<![\w-])(?:(?:INC|SIM)-\d{4}-\d{3}|EV-\d{8}-[0-9a-f]{8})(?!\w)")
# Tramo de dígitos con separadores habituales de un número (espacio . - / ( ) + :), y la coma
# solo pegada a dígitos («612,345,678»; «1, 2, 3» no se une).
_DIGIT_RUN = re.compile(r"\d(?:(?:[\d\s().+/:-]|,(?=\d))*\d)?")
_PHONE_MIN_DIGITS = 9
# Id de alerta de Wazuh: epoch con fracción. Solo se admite en `source.alert_ids`.
_WAZUH_ALERT_ID = re.compile(r"\d{10}\.\d{1,10}")
_IP_PORT = re.compile(r"(?:(?P<v4>[\d.]+)|\[(?P<v6>[0-9A-Fa-f:.]+)\]):(?P<port>\d{1,5})")
_ISO_TIME = r"\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:[+-]\d{2}(?::?\d{2})?)?"
_ISO_8601 = re.compile(rf"(?:\d{{4}}-\d{{2}}-\d{{2}}(?:[T ]{_ISO_TIME})?|{_ISO_TIME})")
_CREDENTIAL_KEY = re.compile(
    r"(?<![A-Za-z0-9])(?P<label>password|passwd|pwd|contrase[ñn]a|clave|secret|token|api[_-]?key"
    r"|access[_-]?key)[\"']?\s*(?P<sep>[=:])(?P<gap>\s*)",
    re.IGNORECASE,
)
# Etiquetas que solo pueden nombrar una credencial: cualquier valor tras ellas se rechaza.
# «clave» y «token» también aparecen en prosa («clave: revisar»), y siguen la regla general.
_UNAMBIGUOUS_LABELS = re.compile(
    r"password|passwd|pwd|contrase[ñn]a|secret|api[_-]?key|access[_-]?key", re.IGNORECASE
)
_UNQUOTED_VALUE = re.compile(r"[^\s\"',;]*")
_LOOKS_SECRET = re.compile(r"[\d_]|[^\w\s]")
_IPV4 = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
_IPV6 = re.compile(r"(?<![\w:])[0-9A-Fa-f]{0,4}(?::[0-9A-Fa-f]{0,4}){2,7}(?![\w:])")


def _has_iban(text: str) -> bool:
    """IBAN sin distinguir mayúsculas y con separadores; el checksum se valida normalizado."""
    for start in _IBAN_START.finditer(text):
        normalized = ""
        for i in range(start.start(), len(text)):
            char = text[i]
            if char in _ALNUM:
                normalized += char.upper()
                follows_alnum = i + 1 < len(text) and text[i + 1] in _ALNUM
                if 15 <= len(normalized) and not follows_alnum and _iban_checksum(normalized):
                    return True
            elif char not in _IBAN_SEPARATORS or text[i - 1] in _IBAN_SEPARATORS:
                break
            if len(normalized) >= 34:
                break
    return False


def _iban_checksum(iban: str) -> bool:
    rearranged = iban[4:] + iban[:4]
    return int("".join(str(int(c, 36)) for c in rearranged)) % 97 == 1


def _has_dni_nie(text: str) -> bool:
    for m in _DNI_NIE.finditer(text):
        if not m["sep"]:
            return True
        prefix = str("XYZ".index(m["body"][0].upper())) if m["body"][0].isalpha() else ""
        number = int(prefix + re.sub(r"\D", "", m["body"]))
        if _CONTROL_LETTERS[number % 23] == m["letter"].upper():
            return True
    return False


def _has_phone(text: str) -> bool:
    """Cualquier tramo de 9+ dígitos (con separadores) es un posible teléfono o identificador
    numérico, salvo que el tramo completo sea una IP o un instante ISO 8601."""
    for m in _DIGIT_RUN.finditer(_OWN_IDS.sub("_", text)):
        run = m.group()
        if sum(c.isdigit() for c in run) < _PHONE_MIN_DIGITS:
            continue
        if not (_is_ip(run) or _is_ip_with_port(run) or _ISO_8601.fullmatch(run)):
            return True
    return False


def _has_credential(text: str) -> bool:
    for m in _CREDENTIAL_KEY.finditer(text):
        rest = text[m.end() :]
        if rest[:1] in ("'", '"'):
            closing = rest.find(rest[0], 1)
            value = rest[1:] if closing < 0 else rest[1:closing]
        else:
            value = _UNQUOTED_VALUE.match(rest).group()
        # Etiqueta inequívoca: cualquier valor. «=» o «:» pegado: basta con 3 caracteres.
        # «: valor» con espacio tras una etiqueta ambigua: solo si parece un secreto (6+
        # caracteres con algún dígito o símbolo): «clave: revisar» no.
        if _UNAMBIGUOUS_LABELS.fullmatch(m["label"]):
            if value.strip():
                return True
        elif m["sep"] == "=" or not m["gap"]:
            if len(value) >= 3:
                return True
        elif len(value) >= 6 and _LOOKS_SECRET.search(value):
            return True
    return False


def _is_ip(candidate: str) -> bool:
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return False
    return True


def _is_ip_with_port(candidate: str) -> bool:
    """`IPv4:puerto` o `[IPv6]:puerto` completos, con IP válida y puerto 1-65535."""
    m = _IP_PORT.fullmatch(candidate)
    if not m or not 1 <= int(m["port"]) <= 65535:
        return False
    return _is_ip(m["v4"] or m["v6"])


def _identifiers(text: str) -> list[str]:
    checks = [
        ("un correo electrónico", lambda t: _EMAIL.search(t) is not None),
        ("un DNI/NIE", _has_dni_nie),
        ("un IBAN", _has_iban),
        ("un teléfono", _has_phone),
        ("una credencial", _has_credential),
    ]
    return [what for what, found in checks if found(text)]


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
