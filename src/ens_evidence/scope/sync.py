"""Qué claves del workspace se sincronizan con `workspace push` y `workspace pull`.

La aprobación nunca viaja: solo la escribe `scope approve` en el workspace.
Las specs futuras amplían esta lista.
"""

from ens_evidence.connectors.config import connector_config_key
from ens_evidence.orgdocs.keys import ANSWERS_KEY, HISTORY_PREFIX, document_keys
from ens_evidence.scope.inventory import ASSETS_KEY, DEPENDENCIES_KEY, SERVICES_KEY, SYSTEM_KEY
from ens_evidence.workspace import WorkspaceStore

QUESTIONNAIRE_KEY = "scope/questionnaire.md"
MEASURES_KEY = "controls/measures.yaml"
CLOUDFLARE_CONFIG_KEY = connector_config_key("cloudflare")
GITHUB_CONFIG_KEY = connector_config_key("github")
GOOGLE_WORKSPACE_CONFIG_KEY = connector_config_key("google-workspace")
RESTIC_CONFIG_KEY = connector_config_key("restic")
ENDPOINT_CONFIG_KEY = connector_config_key("endpoint")

SYNCED_KEYS = (
    ASSETS_KEY,
    SERVICES_KEY,
    DEPENDENCIES_KEY,
    SYSTEM_KEY,
    QUESTIONNAIRE_KEY,
    MEASURES_KEY,
    CLOUDFLARE_CONFIG_KEY,
    GITHUB_CONFIG_KEY,
    GOOGLE_WORKSPACE_CONFIG_KEY,
    RESTIC_CONFIG_KEY,
    ENDPOINT_CONFIG_KEY,
    ANSWERS_KEY,
    *document_keys(),
)

SYNCED_PREFIXES = (HISTORY_PREFIX,)
SCOPE_KEYS = frozenset((ASSETS_KEY, SERVICES_KEY, DEPENDENCIES_KEY, SYSTEM_KEY))
"""Claves que entran en la huella del alcance: solo ellas revocan su aprobación al publicar."""


def synced_keys(store: WorkspaceStore) -> list[str]:
    """Claves sincronizables presentes en `store`: las fijas y todo lo que cuelga de un prefijo."""
    fixed = [key for key in SYNCED_KEYS if store.exists(key)]
    prefixed = [
        key
        for prefix in SYNCED_PREFIXES
        for key in sorted(store.list(prefix))
        if key.startswith(f"{prefix}/")
    ]
    return fixed + prefixed
