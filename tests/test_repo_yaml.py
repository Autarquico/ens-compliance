from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
EXCLUDED = {".venv", ".git", "node_modules"}


def repo_yaml_files():
    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT)
        if path.suffix not in {".yml", ".yaml"} or not path.is_file():
            continue
        if EXCLUDED & set(relative.parts) or relative.parts[:2] == (".claude", "loop"):
            continue
        yield relative


def test_el_repo_contiene_yaml_que_validar():
    assert len(list(repo_yaml_files())) >= 3


@pytest.mark.parametrize("relative", sorted(repo_yaml_files()), ids=str)
def test_todo_yaml_del_repo_es_valido(relative):
    list(yaml.safe_load_all((ROOT / relative).read_text(encoding="utf-8")))
