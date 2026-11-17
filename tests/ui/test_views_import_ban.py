"""D11: el servicio `ui` solo deja peticiones; nunca recoge ni evalúa checks."""

import ast
from pathlib import Path

import ens_evidence.ui as ui_package

FORBIDDEN = {"collect_one", "run_one"}
UI_SOURCES = sorted(Path(ui_package.__file__).parent.rglob("*.py"))


def _names(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom):
            found |= {a.name for a in node.names}
        elif isinstance(node, ast.Import):
            found |= {a.name.rsplit(".", 1)[-1] for a in node.names}
        elif isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
    return found


def test_hay_vistas_que_comprobar():
    assert any(p.parent.name == "views" for p in UI_SOURCES)


def test_ningun_modulo_de_ui_usa_collect_one_ni_run_one():
    offenders = {p.name: sorted(_names(p) & FORBIDDEN) for p in UI_SOURCES if _names(p) & FORBIDDEN}

    assert offenders == {}
