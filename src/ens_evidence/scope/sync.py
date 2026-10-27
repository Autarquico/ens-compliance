"""Qué claves del workspace se sincronizan con `workspace push` y `workspace pull`.

La aprobación nunca viaja: solo la escribe `scope approve` en el workspace.
Las specs futuras amplían esta lista.
"""

from ens_evidence.scope.inventory import ASSETS_KEY, DEPENDENCIES_KEY, SERVICES_KEY, SYSTEM_KEY

QUESTIONNAIRE_KEY = "scope/questionnaire.md"

SYNCED_KEYS = (ASSETS_KEY, SERVICES_KEY, DEPENDENCIES_KEY, SYSTEM_KEY, QUESTIONNAIRE_KEY)
