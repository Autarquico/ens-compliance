import shutil
from pathlib import Path

import pytest
import yaml

from ens_evidence.workspace import LocalStore, S3Store

from .conftest import BUCKET

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "workspace"
KEY = "controls/measures.yaml"


@pytest.fixture
def draft(tmp_path) -> Path:
    path = tmp_path / "draft"
    shutil.copytree(EXAMPLE / "scope", path / "scope")
    return path


def test_workspace_pull_trae_controls_measures(ens, tmp_path):
    ens("controls", "init")
    target = tmp_path / "pulled"

    result = ens("workspace", "pull", str(target))

    assert result.exit_code == 0, result.output
    assert (target / KEY).read_text(encoding="utf-8") == LocalStore(tmp_path / "ws").read_text(KEY)


def test_workspace_push_publica_controls_measures(ens, tmp_path, draft):
    ens("controls", "init")
    ens("workspace", "pull", str(tmp_path / "pulled"))
    (draft / "controls").mkdir()
    shutil.copy(tmp_path / "pulled" / KEY, draft / KEY)
    shutil.rmtree(tmp_path / "ws")

    result = ens("workspace", "push", str(draft))

    assert result.exit_code == 0, result.output
    published = yaml.safe_load(LocalStore(tmp_path / "ws").read_text(KEY))
    assert len(published["measures"]) == 73


def test_pull_y_push_de_controls_measures_viajan_por_s3(ens, s3, tmp_path, draft):
    ens("controls", "init", settings_file=s3)
    pulled = tmp_path / "pulled"

    assert ens("workspace", "pull", str(pulled), settings_file=s3).exit_code == 0
    assert (pulled / KEY).is_file()

    (draft / "controls").mkdir()
    shutil.copy(pulled / KEY, draft / KEY)
    (draft / KEY).write_text((draft / KEY).read_text().replace("UNDETERMINED", "APPLICABLE", 1))
    result = ens("workspace", "push", str(draft), settings_file=s3)

    assert result.exit_code == 0, result.output
    assert "APPLICABLE" in S3Store(BUCKET, "org").read_text(KEY)
