import pytest

from ens_evidence.workspace import SettingsError, load_settings

SENTINEL = "SETTING_SENTINEL"


def write_yaml(path, text):
    path.write_text(text, encoding="utf-8")
    return path


def backup_yaml(repository: str) -> str:
    return (
        "workspace:\n  backend: local\n  local: { path: ws }\n"
        f"backup:\n  restic_repository: '{repository}'\n"
    )


@pytest.mark.parametrize(
    "repository",
    [
        f"rest:https://host/repo?token={SENTINEL}",
        f"s3:https://host/bucket/ens#{SENTINEL}",
        f"rest:https://user:{SENTINEL}@host/repo",
    ],
)
def test_repositorio_con_parametros_o_credenciales_se_rechaza_sin_eco_del_valor(
    tmp_path, repository
):
    path = write_yaml(tmp_path / "s.yaml", backup_yaml(repository))

    with pytest.raises(SettingsError) as info:
        load_settings(path, allow_inside_product=True)

    assert SENTINEL not in str(info.value)
    assert "ENS_BACKUP_" in str(info.value)


@pytest.mark.parametrize(
    "repository",
    [
        "/var/backups/ens",
        "sftp:user@host:/srv/restic",
        "rest:https://host:8000/repo",
        "s3:https://acct.r2.cloudflarestorage.com/bucket/ens",
        "b2:bucket:path",
        "azure:container:/path",
        "gs:bucket:/path",
        "rclone:remote:path",
    ],
)
def test_formatos_documentados_de_restic_se_aceptan(tmp_path, repository):
    path = write_yaml(tmp_path / "s.yaml", backup_yaml(repository))

    assert load_settings(path, allow_inside_product=True).backup.restic_repository == repository
