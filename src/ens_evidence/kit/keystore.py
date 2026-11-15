"""Kit de claves: entradas validadas y paquete de instalación de OpenBao (tar.gz determinista).

Contrato C6 de `.claude/loop/kit-secrets/plan.md`. El paquete no lleva secretos: las partes de
desbloqueo y el token de la app los genera `keystore_setup` dentro del contenedor.
"""

import json
from datetime import datetime
from importlib import resources
from typing import Annotated, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from ens_evidence.kit.backups import BackupInputError
from ens_evidence.kit.bundle import pack

OPENBAO_ADDRESS = "http://openbao:8200"
MAX_TOKEN_PERIOD_HOURS = 72
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class KeystoreInputs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_id: Text
    key_shares: Annotated[int, Field(strict=True, ge=3, le=10)]
    key_threshold: Annotated[int, Field(strict=True, ge=2)]
    max_key_age_days: Annotated[int, Field(strict=True, gt=0)]

    @model_validator(mode="after")
    def _threshold_within_shares(self) -> Self:
        if self.key_threshold > self.key_shares:
            raise ValueError("el umbral M no puede ser mayor que el número de partes N")
        return self


def openbao_file(name: str) -> bytes:
    return (resources.files("ens_evidence.kit") / "openbao" / name).read_bytes()


def _json(data: dict) -> bytes:
    return (json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()


def _connector_fragment(inputs: KeystoreInputs) -> bytes:
    data = {
        "asset_id": inputs.asset_id,
        "address": OPENBAO_ADDRESS,
        "max_key_age_days": inputs.max_key_age_days,
        "max_token_period_hours": MAX_TOKEN_PERIOD_HOURS,
    }
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True).encode()


def render_bundle(inputs: KeystoreInputs, now: datetime) -> bytes:
    """Paquete para la caja: configuración de OpenBao y fragmento del conector, sin secretos."""
    if now.tzinfo is None:
        raise BackupInputError("la fecha del paquete debe llevar zona horaria")
    files = {
        "claves/init.json": _json(
            {"key_shares": inputs.key_shares, "key_threshold": inputs.key_threshold}
        ),
        "claves/keystore.yaml": _connector_fragment(inputs),
        "claves/openbao.hcl": openbao_file("config.hcl"),
    }
    return pack(files, now, root="claves")
