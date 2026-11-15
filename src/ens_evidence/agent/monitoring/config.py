"""`connectors/monitoring.yaml`: qué activo es el VPS y qué hostnames TLS vigilar.

Sin el fichero (o si no es válido) no se sirve ninguna consulta de monitorización y se descarta
todo `mon_*` recibido: la monitorización es opcional y nunca debe romper a los agentes normales.
"""

import logging
import re
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from ens_evidence.connectors.config import (
    ConnectorConfigError,
    connector_config_key,
    load_connector_config,
)
from ens_evidence.workspace import WorkspaceStore

CONFIG_NAME = "monitoring"
MAX_TLS_HOSTNAMES = 16
# Nombre DNS en minúsculas sin puerto ni comodines; es lo único que se interpola en SQL.
HOSTNAME = re.compile(
    r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?"
)
_LOGGER = logging.getLogger("ens_evidence.agent")


def _hostname(value: str) -> str:
    if not HOSTNAME.fullmatch(value):
        raise ValueError("hostname TLS no válido (minúsculas, sin puerto ni comodines)")
    return value


class MonitoringConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vps_asset_id: Annotated[str, Field(pattern=r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")]
    tls_hostnames: list[Annotated[str, AfterValidator(_hostname)]] = Field(
        default_factory=list, max_length=MAX_TLS_HOSTNAMES
    )


def load_monitoring_config(store: WorkspaceStore) -> MonitoringConfig | None:
    """La configuración, o `None` si no existe o no es válida (se avisa en el log)."""
    if not store.exists(connector_config_key(CONFIG_NAME)):
        return None
    try:
        return load_connector_config(store, CONFIG_NAME, MonitoringConfig)
    except ConnectorConfigError:
        _LOGGER.warning("monitoring config invalid; monitoring disabled")
        return None
