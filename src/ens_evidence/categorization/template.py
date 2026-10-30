"""Plantilla de `scope/categorization.yaml` con los servicios del alcance aprobado."""

from ens_evidence.categorization.approval import CATEGORIZATION_KEY
from ens_evidence.scope import load_inventory, require_approved_scope
from ens_evidence.workspace import WorkspaceStore

_HEADER = """\
# Valoración por información y servicio (anexo I, 3 del RD 311/2022).
# Cada dimensión afectada (C, I, T, A, D) lleva un nivel: BAJO, MEDIO o ALTO.
# Si no se ve afectada, usa `level: not_affected` y justifícalo. Sin entrada = pendiente.
#
# BAJO: perjuicio limitado sobre las funciones de la organización, sus activos o los individuos:
#   1.º reducción apreciable de la capacidad de desarrollar sus funciones y competencias;
#   2.º daño menor en los activos; 3.º incumplimiento formal subsanable de una ley o regulación;
#   4.º perjuicio menor a un individuo, fácilmente reparable; 5.º otros de naturaleza análoga.
# MEDIO: perjuicio grave:
#   1.º reducción significativa de la capacidad; 2.º daño significativo en los activos;
#   3.º incumplimiento material, o formal no subsanable; 4.º perjuicio significativo a un
#   individuo, de difícil reparación; 5.º otros de naturaleza análoga.
# ALTO: perjuicio muy grave:
#   1.º anulación efectiva de la capacidad; 2.º daño muy grave, incluso irreparable, en los
#   activos; 3.º incumplimiento grave de una ley o regulación; 4.º perjuicio grave a un
#   individuo, de difícil o imposible reparación; 5.º otros de naturaleza análoga.
# Cada justificación cita el criterio que aplica. El nivel del sistema en cada dimensión es el
# mayor de los de cada información y servicio.
#
# Quién valora: el responsable de la información (information_owner) o del servicio
# (service_owner), art. 11 y 41. La categoría la aprueba el responsable de la seguridad.
#
# Ejemplo de una dimensión:
#     ratings:
#       C:
#         level: MEDIO
#         justification: "Perjuicio grave: incumplimiento material de la normativa de datos."
#     valuation:
#       valued_by: Nombre Apellido
#       role: service_owner
#       valued_at: 2026-01-31T10:00:00+00:00
#
# Informaciones que trata el sistema (id y descripción); añade las tuyas. Una completa:
#     - id: datos-clientes
#       description: Datos de los clientes
#       ratings:
#         C:
#           level: MEDIO
#           justification: "Perjuicio grave: incumplimiento material de la normativa de datos."
#         I: {level: not_affected, justification: "No hay perjuicio por su alteración."}
#         # ...y lo mismo para T, A y D
#       valuation:
#         valued_by: Nombre Apellido
#         role: information_owner
#         valued_at: 2026-01-31T10:00:00+00:00
information: []
services:
"""


def init_categorization(store: WorkspaceStore) -> bool:
    """Crea la plantilla si no existe; False si ya había un fichero (no se sobrescribe).

    Lanza `ScopeNotApproved` si el alcance no está aprobado ahora.
    """
    require_approved_scope(store)
    if store.exists(CATEGORIZATION_KEY):
        return False
    inventory = load_inventory(store).inventory
    assert inventory is not None
    included = set(inventory.system.included_services)
    ids = [s.id for s in inventory.services.services if s.id in included]
    entries = "".join(f"  - id: {service_id}\n    ratings: {{}}\n" for service_id in ids)
    store.write_text(CATEGORIZATION_KEY, _HEADER + (entries or "  []\n"))
    return True
