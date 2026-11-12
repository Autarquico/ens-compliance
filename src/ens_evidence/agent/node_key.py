"""node_key y token de alta por equipo: HMAC sin estado, comparación en tiempo constante."""

import base64
import binascii
import hashlib
import hmac
import re
from dataclasses import dataclass
from datetime import datetime

from ens_evidence.agent.secrets import AgentSecrets

NODE_KEY_PREFIX = "v1."
ENROLL_TOKEN_PREFIX = "e1."
_NODE_DOMAIN = b"ens-osquery-node-key/v1\0"
_ENROLL_DOMAIN = b"ens-osquery-enroll/v1\0"
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_GENERATION = re.compile(r"[0-9]{1,9}")
_EXPIRY = re.compile(r"[0-9]{1,12}")
MAX_HOST_IDENTIFIER = 128


@dataclass(frozen=True)
class NodeIdentity:
    host_identifier: str
    generation: int


def normalize_host_identifier(raw: str) -> str | None:
    """uuid en minúsculas, o `None` si el identificador no tiene esa forma."""
    candidate = raw.strip().lower() if len(raw) <= MAX_HOST_IDENTIFIER else ""
    return candidate if _UUID.fullmatch(candidate) else None


def issue_node_key(secrets: AgentSecrets, host_identifier: str, generation: int) -> str:
    hid = _require_host_identifier(host_identifier)
    mac = _mac(secrets.node_key_secret, _NODE_DOMAIN, hid, str(generation))
    return f"{NODE_KEY_PREFIX}{_b64(hid.encode())}.{generation}.{_b64(mac)}"


def verify_node_key(secrets: AgentSecrets, node_key: str) -> NodeIdentity | None:
    """Identidad del node_key si el MAC es correcto; el llamante comprueba la generación vigente."""
    if not node_key.startswith(NODE_KEY_PREFIX) or len(node_key) > 512:
        return None
    parts = node_key[len(NODE_KEY_PREFIX) :].split(".")
    if len(parts) != 3 or not _GENERATION.fullmatch(parts[1]):
        return None
    hid = _decode_host_identifier(parts[0])
    given = _unb64(parts[2])
    if hid is None or given is None:
        return None
    expected = _mac(secrets.node_key_secret, _NODE_DOMAIN, hid, parts[1])
    if not hmac.compare_digest(expected, given):
        return None
    return NodeIdentity(hid, int(parts[1]))


def issue_enroll_token(
    secrets: AgentSecrets, host_identifier: str, generation: int, expires_at: datetime
) -> str:
    hid = _require_host_identifier(host_identifier)
    expiry = str(int(expires_at.timestamp()))
    mac = _mac(secrets.enroll_key, _ENROLL_DOMAIN, hid, str(generation), expiry)
    return f"{ENROLL_TOKEN_PREFIX}{expiry}.{_b64(mac)}"


def verify_enroll_token(
    secrets: AgentSecrets,
    token: str,
    host_identifier: str,
    generation: int,
    now: datetime,
) -> bool:
    hid = normalize_host_identifier(host_identifier)
    if hid is None or not token.startswith(ENROLL_TOKEN_PREFIX) or len(token) > 256:
        return False
    parts = token[len(ENROLL_TOKEN_PREFIX) :].split(".")
    if len(parts) != 2 or not _EXPIRY.fullmatch(parts[0]):
        return False
    given = _unb64(parts[1])
    if given is None:
        return False
    expected = _mac(secrets.enroll_key, _ENROLL_DOMAIN, hid, str(generation), parts[0])
    mac_ok = hmac.compare_digest(expected, given)
    return mac_ok and int(parts[0]) > now.timestamp()


def _require_host_identifier(raw: str) -> str:
    hid = normalize_host_identifier(raw)
    if hid is None:
        raise ValueError("host_identifier no es un uuid")
    return hid


def _decode_host_identifier(encoded: str) -> str | None:
    raw = _unb64(encoded)
    if raw is None or len(raw) > MAX_HOST_IDENTIFIER:
        return None
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError:
        return None
    return text if normalize_host_identifier(text) == text else None


def _mac(key: bytes, domain: bytes, *fields: str) -> bytes:
    message = domain + b"\0".join(field.encode("ascii") for field in fields)
    return hmac.new(key, message, hashlib.sha256).digest()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes | None:
    try:
        return base64.urlsafe_b64decode(text.encode("ascii") + b"=" * (-len(text) % 4))
    except (binascii.Error, UnicodeEncodeError):
        return None
