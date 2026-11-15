"""Kit de contraseñas: entradas validadas y paquete de instalación (tar.gz determinista).

El paquete solo lleva la configuración del conector `passwords`: ningún secreto. Vaultwarden no
entrega credenciales a la app; el exportador vuelca el JSON que el conector lee.
"""

import re
from datetime import datetime
from typing import Annotated

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from ens_evidence.connectors.passwords.config import EXPORT_PATH
from ens_evidence.kit.backups import BackupInputError
from ens_evidence.kit.bundle import pack

# El exportador corre cada 15 minutos por defecto: 4 vueltas de margen antes de dar el export
# por viejo.
MAX_EXPORT_AGE_MINUTES = 60
_HOST = re.compile(r"(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}")
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class PasswordsInputs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_id: Text
    inactive_days: Annotated[int, Field(strict=True, gt=0)]
    host: str

    @field_validator("host")
    @classmethod
    def _host(cls, value: str) -> str:
        host = value.strip().lower()
        if not _HOST.fullmatch(host):
            raise ValueError("host debe ser un nombre de dominio, sin esquema, puerto ni ruta")
        return host


def _connector_fragment(inputs: PasswordsInputs) -> bytes:
    data = {
        "asset_id": inputs.asset_id,
        "export_path": EXPORT_PATH,
        "inactive_days": inputs.inactive_days,
        "max_export_age_minutes": MAX_EXPORT_AGE_MINUTES,
    }
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True).encode()


def render_bundle(inputs: PasswordsInputs, now: datetime) -> bytes:
    """Paquete para la caja: fragmento del conector, sin secretos."""
    if now.tzinfo is None:
        raise BackupInputError("la fecha del paquete debe llevar zona horaria")
    return pack(
        {"contrasenas/passwords.yaml": _connector_fragment(inputs)}, now, root="contrasenas"
    )
