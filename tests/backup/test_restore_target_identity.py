"""`backup restore` compara el destino con el workspace por identidad de fichero, no por cadena."""

import os

import pytest

from .conftest import ens


def test_el_destino_escrito_con_otras_mayusculas_se_rechaza(
    settings, workspace, scripted_restic, tmp_path
):
    (tmp_path / "ws").mkdir(exist_ok=True)
    if not (tmp_path / "WS").exists():
        pytest.skip("el sistema de ficheros distingue mayúsculas")

    result = ens(
        settings, "backup", "restore", "--snapshot", "latest", "--target", str(tmp_path / "WS/x")
    )

    assert result.exit_code == 1
    assert "dentro del workspace en uso" in result.output
    assert scripted_restic.calls() == []


def test_el_destino_por_un_enlace_a_un_padre_del_workspace_se_rechaza(
    settings, workspace, scripted_restic, tmp_path
):
    (tmp_path / "ws").mkdir(exist_ok=True)
    alias = tmp_path / "alias"
    os.symlink(tmp_path / "ws", alias)

    result = ens(
        settings, "backup", "restore", "--snapshot", "latest", "--target", str(alias / "x")
    )

    assert result.exit_code == 1
    assert scripted_restic.calls() == []
