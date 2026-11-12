"""Configuración del conector `passwords` (`connectors/passwords.yaml`).

Sin secretos: el conector solo lee el JSON que vuelca el exportador del kit. `inactive_days` lo
decide la política de la organización y `max_export_age_minutes` es el margen del exportador.
"""

from pydantic import BaseModel, ConfigDict, Field

from ens_evidence.connectors.restic.config import Days

EXPORT_PATH = "/run/kit/contrasenas/usuarios.json"


class PasswordsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: str = Field(min_length=1)
    export_path: str = Field(default=EXPORT_PATH, min_length=1, max_length=500)
    inactive_days: Days
    max_export_age_minutes: Days
