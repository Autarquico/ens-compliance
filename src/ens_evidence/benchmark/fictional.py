"""Heurística de datos ficticios para el banco: en el repo solo hay pymes inventadas.

Correos, URL y dominios solo de los reservados (`.example`, `.test`, `.invalid`, RFC 2606); IP
solo de documentación o privadas; nunca un DNI/NIE, un NIF de empresa, un IBAN, un teléfono, una
credencial ni algo con forma de secreto. Los mensajes nombran el sitio y el tipo, nunca el valor.
"""

import ipaddress
import json
import re
from collections.abc import Iterator
from typing import Any

from ens_evidence.privacy_text import email_spans, ip_spans, sensitive_problems
from ens_evidence.scope.secrets import find_secrets

RESERVED_TLDS = ("example", "test", "invalid")
_DOCUMENTATION_NETWORKS = tuple(
    ipaddress.ip_network(n)
    for n in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24", "2001:db8::/32")
)
# NIF de persona jurídica: letra de tipo, 7 dígitos y control (dígito o letra A-J).
_COMPANY_NIF = re.compile(
    r"(?<![A-Za-z0-9])[ABCDEFGHJNPQRSUVW][ .-]?\d{7}[ .-]?[0-9A-J](?![A-Za-z0-9])"
)
_URL_HOST = re.compile(r"[a-z][a-z0-9+.-]*://(?:[^/\s@]*@)?(?P<host>[^/\s:?#]+)", re.IGNORECASE)
# Dominio suelto con un TLD real habitual en una pyme española («gestoria-aurora.es»). Los ids
# del catálogo (`mp.com.4`, `mp.info`) empiezan por su grupo y no son dominios.
_BARE_DOMAIN = re.compile(
    r"(?<![\w.-])(?!(?:org|op|mp)\.)(?:[a-z0-9-]+\.)+"
    r"(?:es|com|net|org|eu|io|co|cat|gal|eus|info|biz|dev|app|cloud)(?![\w-])",
    re.IGNORECASE,
)
_PLACEHOLDER = "dato-ficticio"


def text_problems(text: str) -> list[str]:
    """Lo que delata un dato real (o con forma de real) en un texto libre."""
    masked = _mask_allowed(text)
    problems = sensitive_problems(masked)
    if _COMPANY_NIF.search(masked):
        problems.append("contiene un NIF de empresa")
    hosts = [m["host"] for m in _URL_HOST.finditer(masked)]
    hosts += [m.group() for m in _BARE_DOMAIN.finditer(_URL_HOST.sub(" ", masked))]
    if any(not _reserved(host) for host in hosts):
        problems.append("contiene un dominio no reservado (solo .example, .test o .invalid)")
    return problems


def value_problems(value: Any, path: str = "") -> list[str]:
    """Escanea recursivamente los valores ya decodificados (y las claves) de una estructura: un
    escape Unicode o un JSON anidado en un texto no esconden nada."""
    problems = [f"{p or 'texto'}: parece un secreto" for p in find_secrets(value, path)]
    for where, text in _strings(value, path):
        problems += [f"{where or 'texto'}: {problem}" for problem in text_problems(text)]
        nested = _embedded_json(text)
        if nested is not None:
            problems += value_problems(nested, f"{where}<json>")
    return list(dict.fromkeys(problems))


def _strings(value: Any, path: str) -> Iterator[tuple[str, str]]:
    if isinstance(value, dict):
        for key, child in value.items():
            where = f"{path}.{key}" if path else str(key)
            if isinstance(key, str):
                yield f"{where}<clave>", key
            yield from _strings(child, where)
    elif isinstance(value, list | tuple):
        for index, child in enumerate(value):
            yield from _strings(child, f"{path}[{index}]")
    elif isinstance(value, str):
        yield path, value


def _embedded_json(text: str) -> Any:
    stripped = text.strip()
    if stripped[:1] not in ("{", "["):
        return None
    try:
        return json.loads(stripped)
    except ValueError:
        return None


def _mask_allowed(text: str) -> str:
    """Tapa los correos de dominio reservado y las IP de documentación o privadas: son ficticios
    por construcción y el resto de la detección no debe verlos."""
    spans = [s for s in email_spans(text) if _reserved(text[s[0] : s[1]].rsplit("@", 1)[-1])]
    spans += [s for s in ip_spans(text) if _fictional_ip(text[s[0] : s[1]])]
    for start, end in sorted(spans, reverse=True):
        text = text[:start] + _PLACEHOLDER + text[end:]
    return text


def _reserved(host: str) -> bool:
    return host.strip().rstrip(".").lower().rsplit(".", 1)[-1] in RESERVED_TLDS


def _fictional_ip(candidate: str) -> bool:
    address = ipaddress.ip_address(candidate)
    return (
        address.is_private
        or address.is_loopback
        or any(address in network for network in _DOCUMENTATION_NETWORKS)
    )
