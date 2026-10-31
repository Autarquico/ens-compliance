"""El registro de incidentes se organiza sin ciclos: `layout` es el módulo neutral."""

import ast
from pathlib import Path

import ens_evidence.incidents as package

MODULES = sorted(Path(package.__file__).parent.glob("*.py"))


def parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def test_no_module_imports_inside_a_function():
    offenders = [
        f"{path.name}:{node.lineno}"
        for path in MODULES
        for function in ast.walk(parse(path))
        if isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef)
        for node in ast.walk(function)
        if isinstance(node, ast.Import | ast.ImportFrom)
    ]

    assert offenders == []


def test_verify_history_and_layout_do_not_depend_on_store():
    for name in ("verify", "history", "layout", "errors"):
        imported = {
            node.module
            for node in ast.walk(parse(Path(package.__file__).parent / f"{name}.py"))
            if isinstance(node, ast.ImportFrom)
        }

        assert "ens_evidence.incidents.store" not in imported, name


def test_the_incident_asset_prefix_lives_only_in_layout():
    holders = [path.name for path in MODULES if '"incident:"' in path.read_text(encoding="utf-8")]

    assert holders == ["layout.py"]
