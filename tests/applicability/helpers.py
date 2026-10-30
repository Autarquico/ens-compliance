from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml

from ens_evidence.applicability.context import load_context
from ens_evidence.catalog.controls import init_measures, load_measures, save_measures
from ens_evidence.categorization import CATEGORIZATION_KEY, approve_categorization
from ens_evidence.models.enums import Applicability, Role
from ens_evidence.models.readiness import AbsentAssets
from ens_evidence.scope import approve_scope
from ens_evidence.workspace import LocalStore

EXAMPLE_SCOPE = Path(__file__).resolve().parents[2] / "examples" / "workspace" / "scope"
SCOPE_FILES = ("assets", "services", "dependencies", "system")
NOW = datetime(2026, 10, 28, 10, 0, tzinfo=UTC)
LATER = NOW + timedelta(days=1)
SERVICE = "svc-facturacion"
DIMENSIONS = ("C", "I", "T", "A", "D")


ABSENT_REASON = "No hay usuarios externos: el sistema no expone servicios a terceros."


def absent(asset_type: str, justification: str = ABSENT_REASON) -> AbsentAssets:
    return AbsentAssets(asset_type=asset_type, justification=justification)


def uniform(level: str | None) -> dict[str, str | None]:
    return dict.fromkeys(DIMENSIONS, level)


def categorization_document(levels: dict[str, str | None]) -> dict:
    """Un servicio valorado; `None` = dimensión no afectada."""
    ratings = {
        dimension: {
            "level": level or "not_affected",
            "justification": "Perjuicio según los criterios del anexo I, 3.",
        }
        for dimension, level in levels.items()
    }

    def valuation(role: Role) -> dict:
        return {"valued_by": "Marta Gil", "role": role.value, "valued_at": NOW.isoformat()}

    return {
        # La aprobación exige al menos una información; lleva los mismos niveles que el servicio.
        "information": [
            {
                "id": "datos-facturacion",
                "description": "Datos de facturación",
                "ratings": ratings,
                "valuation": valuation(Role.INFORMATION_OWNER),
            }
        ],
        "services": [
            {"id": SERVICE, "ratings": ratings, "valuation": valuation(Role.SERVICE_OWNER)}
        ],
    }


def seed_scope(store: LocalStore) -> None:
    for name in SCOPE_FILES:
        store.write_text(
            f"scope/{name}.yaml", (EXAMPLE_SCOPE / f"{name}.yaml").read_text(encoding="utf-8")
        )
    approve_scope(store, "Ana Ruiz", Role.SYSTEM_OWNER, NOW)


def categorize(store: LocalStore, levels: dict[str, str | None], at: datetime = NOW) -> None:
    """Escribe la valoración y la aprueba como responsable de la seguridad."""
    store.write_text(CATEGORIZATION_KEY, yaml.safe_dump(categorization_document(levels)))
    approve_categorization(store, "Luis Pardo", at)


def prepared_workspace(path, levels: dict[str, str | None]) -> LocalStore:
    store = LocalStore(path)
    seed_scope(store)
    categorize(store, levels)
    init_measures(store)
    return store


def decide(
    store: LocalStore,
    measure_id: str,
    applicability: Applicability,
    justification: str | None = "Decisión de la organización.",
    **extra,
) -> None:
    """Decisión humana tomada a la vista del fundamento vigente, con refuerzos ya elegidos."""
    context = load_context(store, LATER)
    document = load_measures(store)
    assert document is not None
    update = {
        "applicability": applicability,
        "justification": justification,
        "proposed": False,
        "selection": context.basis(measure_id),
        "chosen_reinforcements": first_of_each_group(context, measure_id, applicability),
        **extra,
    }
    updated = [m.model_copy(update=update) if m.id == measure_id else m for m in document.measures]
    save_measures(store, document.model_copy(update={"measures": updated}))


def first_of_each_group(context, measure_id: str, applicability: Applicability) -> list[str]:
    if applicability is not Applicability.APPLICABLE:
        return []
    return [group[0] for group in context.selections[measure_id].alternatives]


def choose_alternatives(store: LocalStore) -> None:
    """Una persona elige el primer refuerzo de cada grupo alternativo de lo que `propose` marcó."""
    context = load_context(store, LATER)
    document = load_measures(store)
    assert document is not None
    updated = [
        m.model_copy(
            update={"chosen_reinforcements": first_of_each_group(context, m.id, m.applicability)}
        )
        for m in document.measures
    ]
    save_measures(store, document.model_copy(update={"measures": updated}))


def measures_by_id(store: LocalStore) -> dict:
    document = load_measures(store)
    assert document is not None
    return {m.id: m for m in document.measures}


def decide_all_undetermined(store: LocalStore) -> list[str]:
    """Una persona decide, con justificación, las medidas que `propose` dejó sin determinar."""
    pending = [
        m_id
        for m_id, m in measures_by_id(store).items()
        if m.applicability is Applicability.UNDETERMINED
    ]
    for measure_id in pending:
        decide(
            store,
            measure_id,
            Applicability.NOT_APPLICABLE,
            "No seleccionada: el anexo II no la exige a este nivel y el riesgo es asumible.",
        )
    choose_alternatives(store)
    return pending
