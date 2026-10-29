"""Qué claves del workspace se sincronizan con `workspace push` y `workspace pull`.

La aprobación nunca viaja: solo la escribe `scope approve` en el workspace.
Las specs futuras amplían esta lista.
"""

from ens_evidence.connectors.config import connector_config_key
from ens_evidence.scope.inventory import ASSETS_KEY, DEPENDENCIES_KEY, SERVICES_KEY, SYSTEM_KEY

QUESTIONNAIRE_KEY = "scope/questionnaire.md"
MEASURES_KEY = "controls/measures.yaml"
CLOUDFLARE_CONFIG_KEY = connector_config_key("cloudflare")
GITHUB_CONFIG_KEY = connector_config_key("github")
RESTIC_CONFIG_KEY = connector_config_key("restic")

SYNCED_KEYS = (
    ASSETS_KEY,
    SERVICES_KEY,
    DEPENDENCIES_KEY,
    SYSTEM_KEY,
    QUESTIONNAIRE_KEY,
    MEASURES_KEY,
    CLOUDFLARE_CONFIG_KEY,
    GITHUB_CONFIG_KEY,
    RESTIC_CONFIG_KEY,
)
