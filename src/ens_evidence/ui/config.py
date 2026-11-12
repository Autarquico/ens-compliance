"""Configuración del servicio `ui`, solo desde el entorno; los errores nunca incluyen valores."""

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

ORIGIN_ENV = "ENS_UI_ORIGIN"
AUD_ENV = "ENS_UI_ACCESS_AUD"
TEAM_DOMAIN_ENV = "ENS_UI_ACCESS_TEAM_DOMAIN"
JWKS_FILE_ENV = "ENS_UI_ACCESS_JWKS_FILE"

_HOST = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")


class UiConfigError(Exception):
    """Falta un ajuste o no es válido; el mensaje nombra la variable, nunca el valor."""


@dataclass(frozen=True)
class UiConfig:
    origin: str
    access_aud: str
    team_domain: str
    jwks_file: Path

    @property
    def issuer(self) -> str:
        return f"https://{self.team_domain}"


def load_ui_config(env: Mapping[str, str] | None = None) -> UiConfig:
    env = os.environ if env is None else env
    return UiConfig(
        origin=_origin(_required(env, ORIGIN_ENV)),
        access_aud=_required(env, AUD_ENV),
        team_domain=_team_domain(_required(env, TEAM_DOMAIN_ENV)),
        jwks_file=Path(_required(env, JWKS_FILE_ENV)).expanduser(),
    )


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise UiConfigError(f"falta {name}")
    return value


def _origin(value: str) -> str:
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        raise UiConfigError(f"{ORIGIN_ENV} no es una URL válida") from None
    clean = (
        parts.scheme == "https"
        and parts.hostname
        and not (parts.username or parts.password or parts.query or parts.fragment)
        and parts.path in ("", "/")
    )
    if not clean:
        raise UiConfigError(f"{ORIGIN_ENV} debe ser un origen https sin ruta (https://host)")
    return f"https://{parts.hostname}" + (f":{port}" if port else "")


def _team_domain(value: str) -> str:
    domain = value.lower()
    if not _HOST.fullmatch(domain):
        raise UiConfigError(
            f"{TEAM_DOMAIN_ENV} debe ser un nombre de host (equipo.cloudflareaccess.com)"
        )
    return domain
