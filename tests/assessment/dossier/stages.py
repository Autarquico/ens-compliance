"""Mundos de aceptación del expediente, por la CLI y sin dobles.

Se construye una vez (por sesión) la cadena completa sobre un mundo READY BÁSICA real y se guarda
una copia del árbol en cada paso; cada test parte de una copia. MEDIA es el mundo de checks.
"""

import atexit
import functools
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

import pytest
from checks.acceptance_helpers import World, make_world
from matrix.acceptance_helpers import Clock, settings_file
from matrix.evaluate_helpers import NOW

from .transition_helpers import (
    CONCLUSIONS,
    DIRECTION,
    SECURITY_OFFICER,
    SYSTEM_OWNER,
    approve_designation,
)
from .worlds import basic_ready_world

D = ["assessment", "dossier"]
ID = re.compile(r"\d{8}T\d{12}Z-[0-9a-f]{12}")
STEP_AT = {
    "generate": NOW,
    "self-assess": NOW + timedelta(hours=1),
    "analyze": NOW + timedelta(hours=2),
    "declare": NOW + timedelta(hours=3),
}
AFTER = NOW + timedelta(hours=4)
STEPS = {
    "self-assess": ["--by", "Ana Pérez", "--role", "system_administrator"],
    "analyze": [
        "--by",
        SECURITY_OFFICER,
        "--conclusion",
        CONCLUSIONS,
        "--elevated-to",
        SYSTEM_OWNER,
        "--elevated",
        "--manual-judgment-review",
    ],
    "declare": ["--by", DIRECTION, "--acknowledge-incomplete"],
}


@dataclass
class Stages:
    """Una copia del árbol tras cada paso, el id del expediente y lo que imprimió cada comando."""

    trees: dict[str, Path] = field(default_factory=dict)
    outputs: dict[str, str] = field(default_factory=dict)
    exit_codes: dict[str, int] = field(default_factory=dict)
    dossier: str = ""


def run_at(world: World, *args: str, at=NOW):
    world.at(at)
    return world.run(*args)


def step(world: World, name: str, dossier: str = ""):
    args = [name, *([dossier] if dossier else []), *STEPS.get(name, [])]
    return run_at(world, *D, *args, at=STEP_AT[name])


def _snapshot_tree(world: World, root: Path, name: str) -> Path:
    destination = root / name
    shutil.copytree(world.store._root.parent, destination)
    return destination


def _chain(world: World, root: Path) -> Stages:
    stages = Stages()
    generated = run_at(world, *D, "generate", at=STEP_AT["generate"])
    assert generated.exit_code == 0, generated.output
    stages.dossier = ID.search(generated.output).group()
    stages.outputs["generate"], stages.exit_codes["generate"] = generated.output, 0
    stages.trees["generated"] = _snapshot_tree(world, root, "generated")
    for name in STEPS:
        result = step(world, name, stages.dossier)
        assert result.exit_code == 0, result.output
        stages.outputs[name], stages.exit_codes[name] = result.output, result.exit_code
        stages.trees[name] = _snapshot_tree(world, root, name)
    return stages


@functools.cache
def basic() -> Stages:
    """BÁSICA: generate → self-assess → analyze → declare, con el árbol tras cada paso."""
    root = Path(tempfile.mkdtemp(prefix="ens-dossier-basic-"))
    atexit.register(shutil.rmtree, root, ignore_errors=True)
    with pytest.MonkeyPatch.context() as monkeypatch:
        world = basic_ready_world(root / "build", monkeypatch)
        return _chain(world, root)


@functools.cache
def media() -> Stages:
    """MEDIA: expediente generado sobre el mundo de checks (designación aprobada)."""
    root = Path(tempfile.mkdtemp(prefix="ens-dossier-media-"))
    atexit.register(shutil.rmtree, root, ignore_errors=True)
    stages = Stages()
    with pytest.MonkeyPatch.context() as monkeypatch:
        world = make_world(root / "build", monkeypatch, "ready")
        approve_designation(world.store)
        generated = run_at(world, *D, "generate", at=STEP_AT["generate"])
        assert generated.exit_code == 0, generated.output
        stages.dossier = ID.search(generated.output).group()
        stages.outputs["generate"] = generated.output
        stages.trees["generated"] = _snapshot_tree(world, root, "generated")
    return stages


def restore(stages: Stages, stage: str, tmp_path: Path, monkeypatch) -> World:
    """Copia del árbol de `stage` como mundo propio del test, con su CLI y su reloj."""
    destination = tmp_path / "world"
    shutil.copytree(stages.trees[stage], destination)
    settings = settings_file(
        destination, {"backend": "local", "local": {"path": str(destination / "ws")}}
    )
    clock = Clock(monkeypatch)
    clock.now = NOW
    return World(destination / "ws", settings, clock)
