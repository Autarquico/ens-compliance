"""Esqueleto mínimo del alcance: lo que crea `init` y lo que completa el primer guardado web."""

from importlib.resources import files

from ens_evidence.scope.sync import QUESTIONNAIRE_KEY
from ens_evidence.workspace import WorkspaceStore

SCOPE_SKELETON = {
    "scope/assets.yaml": "# Un activo por elemento; ver scope/questionnaire.md.\nassets: []\n",
    "scope/services.yaml": "services: []\n",
    "scope/dependencies.yaml": "dependencies: []\n",
    "scope/system.yaml": (
        "# Rellena nombre, descripción y los activos y servicios incluidos o excluidos.\n"
        'name: ""\ndescription: ""\n'
        "included_assets: []\nincluded_services: []\nexclusions: []\n"
    ),
}


def write_if_absent(store: WorkspaceStore, key: str, text: str) -> bool:
    if store.exists(key):
        return False
    store.write_text(key, text)
    return True


def create_scope_skeleton(store: WorkspaceStore) -> list[str]:
    """Crea los ficheros de alcance que faltan sin tocar los existentes; devuelve sus claves."""
    template = files("ens_evidence").joinpath("templates/questionnaire.md").read_text("utf-8")
    skeleton = {**SCOPE_SKELETON, QUESTIONNAIRE_KEY: template}
    return [key for key, text in skeleton.items() if write_if_absent(store, key, text)]
