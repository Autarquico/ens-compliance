"""Configuración del conector de Cloudflare (`connectors/cloudflare.yaml`)."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ACCOUNT_TAG = r"^[0-9a-f]{32}$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PublicRoute(_Strict):
    """Ruta pública por diseño (p. ej. `/agent/*`): se deja a revisión humana."""

    hostname: str = Field(min_length=1)
    path: str = "/*"
    reason: str

    @field_validator("reason")
    @classmethod
    def _justified(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("el motivo no puede estar vacío")
        return value


class CloudflareConfig(_Strict):
    asset_id: str = Field(min_length=1)
    account_id: str = Field(pattern=ACCOUNT_TAG)
    token_kind: Literal["account", "user"] = "account"
    public_routes: list[PublicRoute] = Field(default_factory=list)
    ui_hostname: str | None = None

    @field_validator("ui_hostname")
    @classmethod
    def _bare_hostname(cls, value: str | None) -> str | None:
        """Hostname de la UI sin esquema, puerto, ruta ni comodín."""
        if value is None:
            return None
        name = value.strip().lower().rstrip(".")
        if not re.fullmatch(
            r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+", name
        ):
            raise ValueError("debe ser un hostname sin esquema, puerto, ruta ni comodín")
        return name


class CloudflareRun(CloudflareConfig):
    """Configuración más los datos del inventario que `prepare` resuelve para una recogida."""

    inventory_zones: list[str] = Field(default_factory=list)
