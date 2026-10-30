from datetime import UTC, datetime
from pathlib import Path

import yaml

from ens_evidence.catalog.model import Dimension
from ens_evidence.categorization import CATEGORIZATION_KEY
from ens_evidence.workspace import LocalStore

EXAMPLE_SCOPE = Path(__file__).resolve().parents[2] / "examples" / "workspace" / "scope"
SCOPE_FILES = ("assets", "services", "dependencies", "system")
NOW = datetime(2026, 10, 28, 9, 0, tzinfo=UTC)
VALUED_AT = "2026-10-20T10:00:00+00:00"
JUSTIFICATION = "Criterio 1.º del anexo I, 3"


def ratings(**levels: str | None) -> dict:
    """Una entrada por dimensión dada; `None` la deja pendiente."""
    return {
        dimension: {"level": level, "justification": JUSTIFICATION}
        for dimension, level in levels.items()
        if level is not None
    }


def all_dimensions(level: str) -> dict:
    return ratings(**{dimension.value: level for dimension in Dimension})


def valuation(role: str) -> dict:
    return {"valued_by": "Ana Ruiz", "role": role, "valued_at": VALUED_AT}


def information(id_: str, rated: dict, role: str = "information_owner") -> dict:
    return {
        "id": id_,
        "description": f"Información {id_}",
        "ratings": rated,
        "valuation": valuation(role),
    }


def service(id_: str, rated: dict, role: str = "service_owner") -> dict:
    return {"id": id_, "ratings": rated, "valuation": valuation(role)}


def write_categorization(
    store: LocalStore, services: list[dict], infos: list[dict] | None = None
) -> None:
    """Sin `infos` declara una información en BAJO (la aprobación exige al menos una)."""
    if infos is None:
        infos = [information("datos-facturacion", all_dimensions("BAJO"))]
    document = {"information": infos, "services": services}
    store.write_text(CATEGORIZATION_KEY, yaml.safe_dump(document, allow_unicode=True))


def write_category(store: LocalStore, level: str) -> None:
    write_categorization(store, [service("svc-facturacion", all_dimensions(level))])


def edit(store: LocalStore, name: str, old: str, new: str) -> None:
    key = f"scope/{name}.yaml"
    text = store.read_text(key)
    assert old in text, f"{old!r} not in {key}"
    store.write_text(key, text.replace(old, new, 1))
