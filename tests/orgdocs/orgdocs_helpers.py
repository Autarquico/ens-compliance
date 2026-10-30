import re
from datetime import UTC, datetime
from pathlib import Path

import yaml
from typer.testing import CliRunner

from ens_evidence.cli import app
from ens_evidence.models.enums import Role
from ens_evidence.workspace import LocalStore

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ANSWERS_FIXTURE = FIXTURES / "answers.yaml"
EXAMPLE_SCOPE = Path(__file__).resolve().parents[2] / "examples" / "workspace" / "scope"
SCOPE_FILES = ("assets", "services", "dependencies", "system")
APPROVED_AT = datetime(2026, 10, 28, 9, 30, tzinfo=UTC)
DOCUMENTS = (
    "designacion-responsables",
    "normativa-seguridad",
    "politica-seguridad",
    "procedimientos-seguridad",
    "proceso-autorizacion",
)


SIGNATORY = {
    Role.MANAGEMENT: "Marta Ibáñez Soler",
    Role.SECURITY_OFFICER: "Lucía Navarro Gil",
    Role.SYSTEM_OWNER: "Hugo Serrano Ortiz",
}


def fixture_answers() -> dict:
    return yaml.safe_load(ANSWERS_FIXTURE.read_text(encoding="utf-8"))


def write_answers(store: LocalStore, answers: dict) -> None:
    store.write_text(
        "org/answers.yaml", yaml.safe_dump(answers, allow_unicode=True, sort_keys=False)
    )


def snapshot(store: LocalStore) -> dict[str, bytes]:
    """Todo el contenido del workspace, para comparar antes y después."""
    return {key: store.read_bytes(key) for key in store.list("")}


def unescape(cell: str) -> str:
    return re.sub(r"\\(.)", r"\1", cell)


def traceability_rows(document: str) -> list[tuple[str, str, str]]:
    """(referencia, texto oficial, sección) de la tabla de trazabilidad, sin escapes."""
    rows = []
    for line in document.split("\n## Trazabilidad con el ENS\n", 1)[1].splitlines():
        if line.startswith("| ") and not line.startswith(("| Cláusula", "| ---")):
            cells = [unescape(c.strip()) for c in re.split(r"(?<!\\)\|", line)[1:-1]]
            rows.append((cells[0], cells[1], cells[2]))
    return rows


def ens(settings: Path, *args: str):
    return CliRunner().invoke(app, ["--settings", str(settings), *args])
