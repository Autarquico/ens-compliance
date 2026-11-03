import json

from typer.testing import CliRunner

from ens_evidence import schemas
from ens_evidence.cli import app

runner = CliRunner()


def test_collect_de_conector_desconocido_falla_con_codigo_2_y_lista_los_disponibles():
    result = runner.invoke(app, ["collect", "fake"])
    assert result.exit_code == 2
    assert "Conector desconocido: fake" in result.output


def test_help_funciona():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "schema" in result.output


def test_schema_export_del_repo_no_tiene_deriva():
    result = runner.invoke(app, ["schema", "export", "--check", "--dir", str(schemas_dir())])
    assert result.exit_code == 0, result.output


def schemas_dir():
    from pathlib import Path

    return Path(__file__).resolve().parent.parent / "schemas"


def test_schema_export_escribe_todos_los_contratos_y_luego_check_pasa(tmp_path):
    assert runner.invoke(app, ["schema", "export", "--dir", str(tmp_path)]).exit_code == 0
    written = {p.name for p in tmp_path.glob("*.schema.json")}
    assert written == {f"{name}.schema.json" for name in schemas.CONTRACTS}
    for path in tmp_path.glob("*.schema.json"):
        json.loads(path.read_text())
    assert (
        runner.invoke(app, ["schema", "export", "--check", "--dir", str(tmp_path)]).exit_code == 0
    )


def test_check_detecta_fichero_modificado_ausente_y_sobrante(tmp_path):
    runner.invoke(app, ["schema", "export", "--dir", str(tmp_path)])
    (tmp_path / "review.schema.json").write_text("{}\n")
    (tmp_path / "dossier.schema.json").unlink()
    (tmp_path / "extra.schema.json").write_text("{}\n")
    result = runner.invoke(app, ["schema", "export", "--check", "--dir", str(tmp_path)])
    assert result.exit_code == 1
    assert "review.schema.json" in result.output
    assert "dossier.schema.json" in result.output
    assert "extra.schema.json: unexpected" in result.output


def test_check_sobre_directorio_inexistente_falla(tmp_path):
    result = runner.invoke(app, ["schema", "export", "--check", "--dir", str(tmp_path / "nope")])
    assert result.exit_code == 1
