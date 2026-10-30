from datetime import UTC, datetime
from pathlib import Path

import yaml

from ens_evidence.risk.templates import METHODOLOGY_YAML
from ens_evidence.workspace import LocalStore

EXAMPLE_SCOPE = Path(__file__).resolve().parents[2] / "examples" / "workspace" / "scope"
SCOPE_FILES = ("assets", "services", "dependencies", "system")
NOW = datetime(2026, 10, 28, 9, 0, tzinfo=UTC)
ACCEPTED_AT = "2026-10-20T10:00:00+00:00"
METHODOLOGY = "risk/methodology.yaml"
METHODOLOGY_MD = "risk/methodology.md"
REGISTER = "risk/register.yaml"
PLAN = "risk/treatment-plan.yaml"


def adopted_methodology() -> dict:
    methodology = yaml.safe_load(METHODOLOGY_YAML)
    methodology.update(
        adopted=True,
        name="Metodología de la organización",
        reference="Guía publicada que la respalda",
        adaptation="Escalas de tres niveles",
    )
    return methodology


def acceptance(role: str = "system_owner", at: str = ACCEPTED_AT) -> dict:
    return {"accepted_by": "Ana Ruiz", "role": role, "accepted_at": at}


def risk(id_: str = "R-001", **overrides) -> dict:
    """Riesgo coherente con la metodología de la plantilla: media x alto = alto, residual mínimo."""
    base = {
        "id": id_,
        "assets": ["app-facturas"],
        "services": ["svc-facturacion"],
        "threat": "Acceso no autorizado a facturas",
        "dimensions": ["C"],
        "valuation": {"C": "ALTO"},
        "likelihood": "media",
        "impact": "alto",
        "inherent": "alto",
        "safeguards": ["op.acc.1"],
        "residual": "bajo",
        "treatment": "mitigate",
        "owner": "Ana Ruiz",
    }
    return base | overrides


def action(id_: str = "A-001", **overrides) -> dict:
    base = {
        "id": id_,
        "risk": "R-001",
        "description": "Activar doble factor",
        "owner": "Ana Ruiz",
        "due": "2026-12-31",
        "status": "pending",
    }
    return base | overrides


def dump(store: LocalStore, key: str, document: dict) -> None:
    store.write_text(key, yaml.safe_dump(document, allow_unicode=True))


def write_risks(store: LocalStore, risks: list[dict], actions: list[dict] = ()) -> None:
    dump(store, REGISTER, {"risks": risks})
    dump(store, PLAN, {"actions": list(actions)})


def edit(store: LocalStore, key: str, old: str, new: str) -> None:
    text = store.read_text(key)
    assert old in text, f"{old!r} not in {key}"
    store.write_text(key, text.replace(old, new, 1))


def write_category(store: LocalStore, level: str) -> None:
    """Categorización mínima y completa: una información y el servicio del ejemplo."""
    ratings = {d: {"level": level, "justification": "Criterio 1.º del anexo I, 3"} for d in "CITAD"}
    valuation = {"valued_by": "Ana Ruiz", "valued_at": ACCEPTED_AT}
    dump(
        store,
        "scope/categorization.yaml",
        {
            "information": [
                {
                    "id": "datos",
                    "description": "Datos",
                    "ratings": ratings,
                    "valuation": valuation | {"role": "information_owner"},
                }
            ],
            "services": [
                {
                    "id": "svc-facturacion",
                    "ratings": ratings,
                    "valuation": valuation | {"role": "service_owner"},
                }
            ],
        },
    )
