import json
import os

import pytest

from ens_evidence.workspace.state_files import write_json


def test_write_json_creates_a_private_dir_and_replaces_atomically(tmp_path):
    target = tmp_path / "state" / "a.json"

    write_json(target, {"n": 1})
    write_json(target, {"n": 2})

    assert json.loads(target.read_text("utf-8")) == {"n": 2}
    assert (target.parent.stat().st_mode & 0o777) == 0o700
    assert [p.name for p in target.parent.iterdir()] == ["a.json"]


def test_write_json_refuses_a_group_writable_dir(tmp_path):
    os.chmod(tmp_path, 0o775)

    with pytest.raises(PermissionError):
        write_json(tmp_path / "a.json", {})


def test_write_json_cleans_its_temp_file_when_the_dump_fails(tmp_path):
    with pytest.raises(TypeError):
        write_json(tmp_path / "a.json", {"x": object()})

    assert list(tmp_path.iterdir()) == []
