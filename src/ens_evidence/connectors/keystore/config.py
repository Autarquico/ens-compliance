"""Configuración del conector `keystore` (`connectors/keystore.yaml`).

Sin secretos: el token de la app sale del entorno (`ENS_KEYSTORE_TOKEN`). `max_key_age_days` no
tiene valor por defecto: la rotación aceptable la decide la organización.
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from ens_evidence.connectors.restic.config import Days
from ens_evidence.kit.custody import CustodianRegister

Address = Annotated[str, Field(pattern=r"^https?://[A-Za-z0-9.-]+(:\d{1,5})?/?$")]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class KeystoreConfig(_Strict):
    asset_id: str = Field(min_length=1)
    address: Address = "http://openbao:8200"
    max_key_age_days: Days
    # 72 h es el periodo del rol `ens-app` que crea el kit; no es una decisión de la organización
    max_token_period_hours: Days = 72


class KeystoreRun(KeystoreConfig):
    """Configuración más el registro de custodios que `prepare` lee del workspace."""

    custody: CustodianRegister | None = None
    unreadable_registers: list[str] = Field(default_factory=list)
