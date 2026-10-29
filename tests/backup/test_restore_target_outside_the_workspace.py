import pytest

from .conftest import ens


@pytest.mark.parametrize("relative", ["", "restauracion", "a/b"])
def test_restore_dentro_del_workspace_en_uso_se_rechaza_sin_ejecutar_restic(
    settings, workspace, scripted_restic, tmp_path, relative
):
    target = tmp_path / "ws" / relative

    result = ens(settings, "backup", "restore", "--snapshot", "latest", "--target", str(target))

    assert result.exit_code == 1
    assert "dentro del workspace en uso" in result.output
    assert scripted_restic.calls() == []
