import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv no está instalado")
def test_el_wheel_incluye_los_datos_y_la_instantanea_del_catalogo(tmp_path):
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(tmp_path)],
        cwd=REPO,
        check=True,
        capture_output=True,
    )

    wheel = next(tmp_path.glob("ens_evidence-*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
    for expected in (
        "ens_evidence/catalog/data/org.yaml",
        "ens_evidence/catalog/data/op.yaml",
        "ens_evidence/catalog/data/mp.yaml",
        "ens_evidence/catalog/source/anexo-ii.html",
        "ens_evidence/catalog/source/anexo-ii.meta.yaml",
    ):
        assert expected in names
