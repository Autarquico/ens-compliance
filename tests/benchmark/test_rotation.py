"""Perfiles por partición y recuentos de D7 de la reservada."""

import builtins
import shutil
from pathlib import Path

from typer.testing import CliRunner

from ens_evidence.benchmark.access import MEASUREMENTS_FILE
from ens_evidence.benchmark.cases import validate_bench
from ens_evidence.benchmark.model import Partition
from ens_evidence.cli import app

from .test_cases import bench, case, judgment_defect, profile, write_yaml

runner = CliRunner()


def with_holdout(root: Path) -> Path:
    """Un banco con un caso de dev y otro de la reservada, cada uno con su perfil `mini`."""
    bench(root)
    shutil.copytree(root / "profiles/dev", root / "profiles/holdout")
    shutil.copytree(root / "cases/dev/files", root / "cases/holdout/files")
    write_yaml(root / "cases/holdout/reservado.yaml", case(id="reservado", partition="holdout"))
    shutil.copytree(
        root / "cases/dev/files/inventario-sin-responsable",
        root / "cases/holdout/files/reservado",
    )
    return root


def test_validar_dev_no_abre_ningun_fichero_de_la_reservada(tmp_path, monkeypatch):
    root = with_holdout(tmp_path)
    opened: list[Path] = []
    real_open = builtins.open
    real_read_text, real_read_bytes = Path.read_text, Path.read_bytes

    def spy_open(file, *args, **kwargs):
        opened.append(Path(file))
        return real_open(file, *args, **kwargs)

    def spy_read_text(self, *args, **kwargs):
        opened.append(self)
        return real_read_text(self, *args, **kwargs)

    def spy_read_bytes(self, *args, **kwargs):
        opened.append(self)
        return real_read_bytes(self, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", spy_open)
    monkeypatch.setattr(Path, "read_text", spy_read_text)
    monkeypatch.setattr(Path, "read_bytes", spy_read_bytes)

    assert validate_bench(root, Partition.DEV) == []

    inside = [p for p in opened if p.is_relative_to(root)]
    assert inside
    assert not [p for p in inside if "holdout" in p.relative_to(root).parts]


def test_un_caso_de_dev_no_ve_un_perfil_de_la_reservada(tmp_path):
    root = bench(tmp_path)
    shutil.move(root / "profiles/dev", root / "profiles/holdout")

    problems = validate_bench(root, Partition.DEV)

    assert any("el perfil mini no existe o no es válido" in p for p in problems)


def test_un_perfil_solo_esta_en_su_particion(tmp_path):
    root = with_holdout(tmp_path)
    write_yaml(root / "profiles/holdout/mini/profile.yaml", profile() | {"title": "Otra Ficticia"})

    assert validate_bench(root, Partition.DEV) == []
    assert validate_bench(root, Partition.HOLDOUT) == []


def measure(root: Path):
    return runner.invoke(
        app,
        ["benchmark", "validate", "--cases", str(root), "--partition", "holdout", "--measurement"],
    )


def test_medir_la_reservada_informa_de_los_recuentos_y_falla_por_debajo_del_minimo(tmp_path):
    root = with_holdout(tmp_path)

    result = measure(root)

    assert result.exit_code == 2
    assert "1 defectos de juicio, 1 deterministas, 0 expedientes correctos" in result.output
    assert "defectos de juicio: 1 (la muestra exige al menos 20)" in result.output
    assert "no hay expedientes correctos" in result.output
    assert MEASUREMENTS_FILE in [p.name for p in root.iterdir()]


def test_medir_la_reservada_rechaza_un_perfil_que_no_es_basico(tmp_path):
    root = with_holdout(tmp_path)
    medium = profile()
    medium["categorization"]["C"] = "MEDIO"
    write_yaml(root / "profiles/holdout/mini/profile.yaml", medium)

    result = measure(root)

    assert "perfil mini: no es de categoría BÁSICA" in result.output


def test_sin_measurement_la_reservada_sigue_cerrada(tmp_path):
    root = with_holdout(tmp_path)

    result = runner.invoke(
        app, ["benchmark", "validate", "--cases", str(root), "--partition", "holdout"]
    )

    assert result.exit_code == 2
    assert "--measurement" in result.output


def test_un_defecto_de_juicio_extra_sube_el_recuento(tmp_path):
    root = with_holdout(tmp_path)
    extra = judgment_defect(id="otro", expected_criterion="calidad-documental")
    data = case(id="reservado", partition="holdout")
    data["defects"] = [judgment_defect(), extra]
    write_yaml(root / "cases/holdout/reservado.yaml", data)

    assert "2 defectos de juicio" in measure(root).output
