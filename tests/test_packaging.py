import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv no está instalado")
def test_el_wheel_incluye_el_cuestionario(tmp_path):
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(tmp_path)],
        cwd=REPO,
        check=True,
        capture_output=True,
    )

    wheel = next(tmp_path.glob("ens_evidence-*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        assert "ens_evidence/templates/questionnaire.md" in archive.namelist()
