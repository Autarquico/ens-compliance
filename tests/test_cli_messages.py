import re
from pathlib import Path

import typer.main
import yaml
from typer.testing import CliRunner

from ens_evidence.cli import app

runner = CliRunner()
SOURCES = Path(__file__).resolve().parent.parent / "src" / "ens_evidence"
CITED = re.compile(r"ens-evidence((?: [a-z][a-z-]*)+)")


def ens(settings: Path, *args: str):
    return runner.invoke(app, ["--settings", str(settings), *args])


def empty_workspace_settings(tmp_path: Path) -> Path:
    path = tmp_path / "settings.yaml"
    path.write_text(
        yaml.safe_dump({"workspace": {"backend": "local", "local": {"path": str(tmp_path / "ws")}}})
    )
    return path


def is_group(command) -> bool:
    return hasattr(command, "commands")


def resolve(words: list[str]):
    """Comando al que llega la cita; las palabras tras un comando hoja son prosa."""
    root = typer.main.get_command(app)
    command = root
    for word in words:
        if not is_group(command):
            break
        child = command.commands.get(word)
        if child is None:
            return None if command is root else command
        command = child
    return command


def cited_commands() -> list[tuple[Path, str]]:
    files = [*(SOURCES / "scope").rglob("*.py"), SOURCES / "cli.py"]
    return [
        (file, match.group(1).split())
        for file in files
        for match in CITED.finditer(file.read_text())
    ]


def test_workspace_push_de_carpeta_inexistente_recomienda_pull_y_no_publica(tmp_path):
    settings = empty_workspace_settings(tmp_path)
    missing = tmp_path / "no-existe"

    result = ens(settings, "workspace", "push", str(missing))

    assert result.exit_code == 1
    assert "ens-evidence workspace pull" in result.output
    assert not (tmp_path / "ws").exists()
    assert not missing.exists()


def test_fichero_de_scope_ausente_recomienda_init(tmp_path):
    settings = empty_workspace_settings(tmp_path)

    result = ens(settings, "inventory", "validate")

    assert result.exit_code != 0
    assert "ens-evidence init" in result.output


def test_todo_comando_citado_en_los_mensajes_existe_en_la_cli():
    cited = cited_commands()
    assert cited, "no se encontró ninguna cita `ens-evidence ...`"
    for file, words in cited:
        command = resolve(words)
        assert command is not None and not is_group(command), (
            f"{file.name}: `ens-evidence {' '.join(words)}` no existe en la CLI"
        )
