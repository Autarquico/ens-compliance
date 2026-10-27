from datetime import UTC, datetime
from pathlib import Path

from ens_evidence.workspace import LocalStore

EXAMPLE_SCOPE = Path(__file__).resolve().parents[2] / "examples" / "workspace" / "scope"
SCOPE_FILES = ("assets", "services", "dependencies", "system")
APPROVED_AT = datetime(2026, 10, 27, 9, 0, tzinfo=UTC)
TOKEN = "ghp_" + "A1b2C3d4E5" * 4


def edit(store: LocalStore, name: str, old: str, new: str) -> None:
    key = f"scope/{name}.yaml"
    text = store.read_text(key)
    assert old in text, f"{old!r} not in {key}"
    store.write_text(key, text.replace(old, new, 1))
