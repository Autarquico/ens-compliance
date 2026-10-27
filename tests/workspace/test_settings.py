from pathlib import Path

import pytest

from ens_evidence.workspace import (
    LocalStore,
    S3Store,
    SettingsError,
    load_settings,
    open_store,
    resolve_settings_path,
)


def write_yaml(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


S3_YAML = """
workspace:
  backend: s3
  s3:
    bucket: evidencias
    prefix: org
    endpoint_url: {endpoint}
{extra}
"""


def s3_yaml(endpoint="https://r2.example.com", extra=""):
    return S3_YAML.format(endpoint=endpoint, extra=extra)


def test_ruta_explicita_gana_sobre_env_y_defecto(tmp_path):
    env = {"ENS_SETTINGS": str(tmp_path / "env.yaml")}

    assert resolve_settings_path(tmp_path / "cli.yaml", env) == tmp_path / "cli.yaml"


def test_env_gana_sobre_el_defecto(tmp_path):
    env = {"ENS_SETTINGS": str(tmp_path / "env.yaml")}

    assert resolve_settings_path(None, env) == tmp_path / "env.yaml"


def test_sin_flag_ni_env_se_usa_el_fichero_de_usuario():
    expected = Path("~/.config/ens-evidence/settings.yaml").expanduser()

    assert resolve_settings_path(None, {}) == expected


def test_la_ruta_por_defecto_no_depende_del_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    assert resolve_settings_path(None, {}).is_absolute()
    assert not str(resolve_settings_path(None, {})).startswith(str(tmp_path))


def test_ruta_local_relativa_se_resuelve_respecto_al_fichero_de_ajustes(tmp_path):
    settings_file = write_yaml(
        tmp_path / "conf" / "s.yaml", "workspace:\n  backend: local\n  local:\n    path: ../datos\n"
    )

    settings = load_settings(settings_file)

    assert settings.workspace.local.path == (tmp_path / "datos").resolve()


def test_ruta_relativa_no_depende_del_cwd(tmp_path, monkeypatch):
    settings_file = write_yaml(
        tmp_path / "conf" / "s.yaml", "workspace:\n  backend: local\n  local:\n    path: datos\n"
    )
    monkeypatch.chdir(tmp_path.parent)

    resolved = load_settings(settings_file).workspace.local.path

    assert resolved == (tmp_path / "conf" / "datos").resolve()


def test_sin_fichero_de_ajustes_no_hay_destino_por_defecto(tmp_path):
    with pytest.raises(SettingsError):
        load_settings(tmp_path / "no-existe.yaml")


def test_ajustes_sin_seccion_del_backend_elegido_se_rechazan(tmp_path):
    path = write_yaml(tmp_path / "s.yaml", "workspace:\n  backend: s3\n")

    with pytest.raises(SettingsError, match="s3"):
        load_settings(path)


def test_ajustes_vacios_o_sin_workspace_se_rechazan(tmp_path):
    with pytest.raises(SettingsError):
        load_settings(write_yaml(tmp_path / "a.yaml", ""))
    with pytest.raises(SettingsError):
        load_settings(write_yaml(tmp_path / "b.yaml", "otro: 1\n"))


@pytest.mark.parametrize(
    "extra_line",
    [
        "    secret_access_key: SUPERSECRETO123",
        "    accessKeyId: SUPERSECRETO123",
        "    session-token: SUPERSECRETO123",
        "    password: SUPERSECRETO123",
    ],
)
def test_credenciales_en_yaml_se_rechazan_sin_mostrar_el_valor(tmp_path, extra_line):
    path = write_yaml(tmp_path / "s.yaml", s3_yaml(extra=extra_line))

    with pytest.raises(SettingsError) as info:
        load_settings(path)

    assert "SUPERSECRETO123" not in str(info.value)


def test_credenciales_en_endpoint_url_se_rechazan_sin_mostrar_el_valor(tmp_path):
    path = write_yaml(
        tmp_path / "s.yaml", s3_yaml(endpoint="https://AKIDUSER:SUPERSECRETO123@r2.example.com")
    )

    with pytest.raises(SettingsError) as info:
        load_settings(path)

    message = str(info.value)
    assert "SUPERSECRETO123" not in message
    assert "AKIDUSER" not in message


def test_endpoint_http_se_rechaza_salvo_allow_insecure_http(tmp_path):
    http = "http://localhost:9000"
    with pytest.raises(SettingsError):
        load_settings(write_yaml(tmp_path / "a.yaml", s3_yaml(endpoint=http)))

    permitido = write_yaml(
        tmp_path / "b.yaml", s3_yaml(endpoint=http, extra="    allow_insecure_http: true")
    )

    assert load_settings(permitido).workspace.s3.endpoint_url == http


def test_endpoint_https_se_acepta(tmp_path):
    settings = load_settings(write_yaml(tmp_path / "s.yaml", s3_yaml()))

    assert settings.workspace.s3.endpoint_url == "https://r2.example.com"


def test_destino_local_dentro_del_checkout_del_producto_se_rechaza(tmp_path):
    checkout = tmp_path / "checkout"
    (checkout).mkdir()
    (checkout / "pyproject.toml").write_text('[project]\nname = "ens-evidence"\n', encoding="utf-8")
    path = write_yaml(
        tmp_path / "s.yaml",
        f"workspace:\n  backend: local\n  local:\n    path: {checkout / 'datos'}\n",
    )

    with pytest.raises(SettingsError):
        load_settings(path)


def test_destino_dentro_del_producto_se_permite_solo_con_el_parametro_de_api(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "pyproject.toml").write_text('[project]\nname = "ens-evidence"\n', encoding="utf-8")
    path = write_yaml(
        tmp_path / "s.yaml",
        f"workspace:\n  backend: local\n  local:\n    path: {checkout / 'datos'}\n",
    )

    settings = load_settings(path, allow_inside_product=True)

    assert settings.workspace.local.path == (checkout / "datos").resolve()


def test_pyproject_de_otro_proyecto_no_cuenta_como_checkout_del_producto(tmp_path):
    otro = tmp_path / "otro"
    otro.mkdir()
    (otro / "pyproject.toml").write_text('[project]\nname = "otra-cosa"\n', encoding="utf-8")
    path = write_yaml(
        tmp_path / "s.yaml",
        f"workspace:\n  backend: local\n  local:\n    path: {otro / 'datos'}\n",
    )

    assert load_settings(path).workspace.local.path == (otro / "datos").resolve()


def test_open_store_construye_el_backend_indicado(tmp_path):
    local = write_yaml(
        tmp_path / "l.yaml",
        f"workspace:\n  backend: local\n  local:\n    path: {tmp_path / 'ws'}\n",
    )

    assert isinstance(open_store(load_settings(local)), LocalStore)


def test_open_store_s3(tmp_path, monkeypatch):
    import boto3

    monkeypatch.setattr(boto3, "client", lambda *_a, **_k: object())
    settings = load_settings(write_yaml(tmp_path / "s.yaml", s3_yaml()))

    assert isinstance(open_store(settings), S3Store)
