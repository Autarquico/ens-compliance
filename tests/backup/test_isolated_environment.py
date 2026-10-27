import subprocess

import pytest

from ens_evidence.backup import BackupError, BackupSecrets, Restic, load_secrets, subprocess_env

from .conftest import (
    BACKUP_KEY,
    BACKUP_SECRET,
    PASSWORD,
    WORKSPACE_KEY,
    WORKSPACE_SECRET,
    ens,
)

SECRETS = BackupSecrets(PASSWORD, BACKUP_KEY, BACKUP_SECRET)
WORKSPACE_ENV = {
    "AWS_ACCESS_KEY_ID": WORKSPACE_KEY,
    "AWS_SECRET_ACCESS_KEY": WORKSPACE_SECRET,
    "AWS_SESSION_TOKEN": "token-del-workspace",
    "AWS_ENDPOINT_URL": "https://workspace.example",
    "AWS_DEFAULT_REGION": "region-del-workspace",
    "ENS_SETTINGS": "/ruta/ajustes.yaml",
    "PATH": "/usr/bin:/bin",
    "HOME": "/home/ana",
}


def test_entorno_del_subproceso_solo_lleva_variables_mapeadas_path_y_home():
    env = subprocess_env(SECRETS, WORKSPACE_ENV)

    assert env == {
        "RESTIC_PASSWORD": PASSWORD,
        "AWS_ACCESS_KEY_ID": BACKUP_KEY,
        "AWS_SECRET_ACCESS_KEY": BACKUP_SECRET,
        "PATH": "/usr/bin:/bin",
        "HOME": "/home/ana",
    }


def test_ninguna_variable_del_workspace_llega_al_entorno_de_restic():
    env = subprocess_env(SECRETS, WORKSPACE_ENV)

    assert WORKSPACE_KEY not in env.values()
    assert WORKSPACE_SECRET not in env.values()
    assert not {"AWS_SESSION_TOKEN", "AWS_ENDPOINT_URL", "ENS_SETTINGS"} & env.keys()


def test_region_de_la_copia_llega_como_aws_default_region():
    env = subprocess_env(SECRETS, WORKSPACE_ENV, region="auto")

    assert env["AWS_DEFAULT_REGION"] == "auto"


def test_sin_region_en_la_copia_no_se_hereda_la_del_workspace():
    assert "AWS_DEFAULT_REGION" not in subprocess_env(SECRETS, WORKSPACE_ENV)


def test_restic_se_invoca_con_lista_de_argumentos_sin_shell(monkeypatch, fake_restic):
    calls = []
    real_run = subprocess.run

    def spy(*args, **kwargs):
        calls.append((args, kwargs))
        return real_run(*args, **kwargs)

    monkeypatch.setattr("ens_evidence.backup.restic.subprocess.run", spy)

    Restic(
        "/repo; rm -rf /", SECRETS, base_env=WORKSPACE_ENV | {"PATH": _fake_path(fake_restic)}
    ).init()

    (args, kwargs), *_ = calls
    assert isinstance(args[0], list)
    assert args[0][1:3] == ["--repo", "/repo; rm -rf /"]
    assert kwargs["shell"] is False


def _fake_path(dump) -> str:
    return f"{dump.parent / 'fakebin'}:/usr/bin:/bin"


def test_el_proceso_restic_real_no_recibe_variables_del_workspace(fake_restic, monkeypatch):
    for name, value in WORKSPACE_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("PATH", _fake_path(fake_restic))

    Restic("/repo", SECRETS, region="auto").init()

    seen = fake_restic.read_text()
    assert f"RESTIC_PASSWORD={PASSWORD}" in seen
    assert f"AWS_ACCESS_KEY_ID={BACKUP_KEY}" in seen
    assert "AWS_DEFAULT_REGION=auto" in seen
    for leaked in (WORKSPACE_KEY, WORKSPACE_SECRET, "token-del-workspace", "workspace.example"):
        assert leaked not in seen
    assert "ENS_SETTINGS" not in seen


def test_credenciales_de_copia_iguales_a_las_del_workspace_se_rechazan():
    env = {
        "ENS_BACKUP_RESTIC_PASSWORD": PASSWORD,
        "ENS_BACKUP_AWS_ACCESS_KEY_ID": WORKSPACE_KEY,
        "ENS_BACKUP_AWS_SECRET_ACCESS_KEY": BACKUP_SECRET,
        "AWS_ACCESS_KEY_ID": WORKSPACE_KEY,
        "AWS_SECRET_ACCESS_KEY": WORKSPACE_SECRET,
    }

    with pytest.raises(BackupError, match="coincide con la credencial del workspace") as raised:
        load_secrets("s3:https://host/bucket/repo", env)

    assert WORKSPACE_KEY not in str(raised.value)


def test_secreto_de_copia_igual_al_del_workspace_se_rechaza():
    env = {
        "ENS_BACKUP_RESTIC_PASSWORD": PASSWORD,
        "ENS_BACKUP_AWS_ACCESS_KEY_ID": BACKUP_KEY,
        "ENS_BACKUP_AWS_SECRET_ACCESS_KEY": WORKSPACE_SECRET,
        "AWS_SECRET_ACCESS_KEY": WORKSPACE_SECRET,
    }

    with pytest.raises(BackupError, match="coincide"):
        load_secrets("s3:https://host/bucket/repo", env)


def test_backup_run_con_credenciales_iguales_sale_con_error_sin_ejecutar_restic(
    tmp_path, monkeypatch, fake_restic
):
    import yaml

    path = tmp_path / "settings.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "workspace": {"backend": "local", "local": {"path": str(tmp_path / "ws")}},
                "backup": {"restic_repository": "s3:https://host/bucket/repo"},
            }
        )
    )
    monkeypatch.setenv("ENS_BACKUP_RESTIC_PASSWORD", PASSWORD)
    for var in ("ACCESS_KEY_ID", "SECRET_ACCESS_KEY"):
        monkeypatch.setenv(f"ENS_BACKUP_AWS_{var}", "mismo-valor")
        monkeypatch.setenv(f"AWS_{var}", "mismo-valor")

    result = ens(path, "backup", "run")

    assert result.exit_code != 0
    assert "distintas" in result.output
    assert not fake_restic.exists()


def test_restic_ausente_se_informa_sin_secretos(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))

    with pytest.raises(BackupError, match="no está instalado"):
        Restic("/repo", SECRETS, base_env={"PATH": str(tmp_path)}).init()
