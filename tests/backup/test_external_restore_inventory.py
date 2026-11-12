"""Inventario de `record-kit`: lo que no se puede comparar no da una restauración por buena."""

import os

import pytest

from ens_evidence.backup.errors import BackupError
from ens_evidence.backup.external_restore import differences, inventory_of_tree


def test_un_enlace_a_directorio_del_original_no_desaparece_del_inventario(tmp_path):
    original, restored = tmp_path / "original", tmp_path / "restaurado"
    for root in (original, restored):
        root.mkdir()
        (root / "a.txt").write_text("uno")
    (tmp_path / "fuera").mkdir()
    (original / "enlace").symlink_to(tmp_path / "fuera", target_is_directory=True)

    assert inventory_of_tree(original)["enlace"] == "no-regular"
    assert differences(inventory_of_tree(restored), inventory_of_tree(original)) == [
        "falta en lo restaurado: enlace"
    ]


@pytest.mark.skipif(os.geteuid() == 0, reason="root lee carpetas sin permiso")
def test_una_carpeta_que_no_se_puede_leer_para_el_inventario(tmp_path):
    (tmp_path / "cerrada").mkdir()
    (tmp_path / "cerrada" / "b.txt").write_text("dos")
    (tmp_path / "cerrada").chmod(0)
    try:
        with pytest.raises(BackupError, match="no se puede recorrer"):
            inventory_of_tree(tmp_path)
    finally:
        (tmp_path / "cerrada").chmod(0o700)
