"""Secretos del servicio `agent`, solo desde el entorno; nunca se muestran sus valores."""

import hmac
import os
from collections.abc import Mapping
from dataclasses import dataclass, field

ENROLL_KEY_ENV = "ENS_AGENT_ENROLL_KEY"
NODE_KEY_SECRET_ENV = "ENS_AGENT_NODE_KEY_SECRET"
MIN_SECRET_BYTES = 32


class AgentSecretsError(Exception):
    """Falta un secreto o no es aceptable; el mensaje nombra la variable, nunca el valor."""


@dataclass(frozen=True)
class AgentSecrets:
    enroll_key: bytes = field(repr=False)
    node_key_secret: bytes = field(repr=False)

    def __repr__(self) -> str:
        return "AgentSecrets(enroll_key=***, node_key_secret=***)"

    __str__ = __repr__


def load_agent_secrets(env: Mapping[str, str] | None = None) -> AgentSecrets:
    env = os.environ if env is None else env
    enroll = _read(env, ENROLL_KEY_ENV)
    node = _read(env, NODE_KEY_SECRET_ENV)
    if hmac.compare_digest(enroll, node):
        raise AgentSecretsError(f"{ENROLL_KEY_ENV} y {NODE_KEY_SECRET_ENV} deben ser distintos")
    return AgentSecrets(enroll_key=enroll, node_key_secret=node)


def _read(env: Mapping[str, str], name: str) -> bytes:
    value = env.get(name)
    if not value:
        raise AgentSecretsError(f"falta {name}")
    raw = value.encode("utf-8")
    if len(raw) < MIN_SECRET_BYTES:
        raise AgentSecretsError(f"{name} debe tener al menos {MIN_SECRET_BYTES} bytes")
    return raw
