"""Detección de datos personales y credenciales en texto libre.

Compartido por el registro de incidentes y el expediente. Los mensajes nombran el tipo de dato,
nunca el valor.
"""

import ipaddress
import re
import string

from ens_evidence.scope.secrets import find_secrets

NAME_MAX = 120

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
        if not (
            _is_ip(run) or _is_ip_with_port(run) or _ISO_8601.fullmatch(run) or _is_ip_list(run)
        ):
            return True
    return False


def has_credential(text: str) -> bool:
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


def _is_ip_list(candidate: str) -> bool:
    """Lista de IP separadas por comas («203.0.113.7,198.51.100.2»): cada elemento es una IP."""
    parts = [part.strip() for part in candidate.split(",")]
    return len(parts) > 1 and all(_is_ip(p) or _is_ip_with_port(p) for p in parts)


def _is_ip_with_port(candidate: str) -> bool:
    """`IPv4:puerto` o `[IPv6]:puerto` completos, con IP válida y puerto 1-65535."""
    m = _IP_PORT.fullmatch(candidate)
    if not m or not 1 <= int(m["port"]) <= 65535:
        return False
    return _is_ip(m["v4"] or m["v6"])


def _has_email(text: str) -> bool:
    return _EMAIL.search(text) is not None


def _identifiers(text: str) -> list[str]:
    checks = [
        ("un correo electrónico", _has_email),
        ("un DNI/NIE", _has_dni_nie),
        ("un IBAN", _has_iban),
        ("un teléfono", _has_phone),
        ("una credencial", has_credential),
    ]
    return [what for what, found in checks if found(text)]


def _has_ip(text: str) -> bool:
    return any(_is_ip(m.group()) for m in _IPV4.finditer(text)) or any(
        m.group().count(":") >= 2 and _is_ip(m.group()) for m in _IPV6.finditer(text)
    )


def text_problems(text: str) -> tuple[list[str], list[str]]:
    """(errores, avisos) de un texto libre: «contiene <tipo>» y, para una IP, un aviso."""
    errors = [f"contiene {what}" for what in _identifiers(text)]
    warnings = (
        ["contiene una dirección IP, que puede ser un dato personal"] if _has_ip(text) else []
    )
    return errors, warnings


def check_declared_name(name: str) -> list[str]:
    """Errores de `--by`: un nombre declarado no puede ser vacío, largo, un secreto ni un
    identificador personal (correo, DNI/NIE, IBAN, teléfono). Nunca incluye el valor."""
    if not name.strip():
        return ["--by: indica quién registra (nombre declarado)"]
    errors = (
        [f"--by: demasiado largo (máximo {NAME_MAX} caracteres)"] if len(name) > NAME_MAX else []
    )
    errors += [f"{p}: parece un secreto; quítalo" for p in find_secrets(name, "--by")]
    errors += [f"--by: {problem}; indica solo un nombre" for problem in text_problems(name)[0]]
    return errors
