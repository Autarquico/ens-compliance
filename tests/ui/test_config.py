import pytest
import typer
from typer.testing import CliRunner

from ens_evidence.ui.config import UiConfigError, load_ui_config


def _serve():
    from ens_evidence.ui.cli import ui_app

    root = typer.Typer()
    root.add_typer(ui_app, name="ui")
    return CliRunner().invoke(root, ["ui", "serve"])


GOOD = {
    "ENS_UI_ORIGIN": "https://ui.example.com",
    "ENS_UI_ACCESS_AUD": "aud",
    "ENS_UI_ACCESS_TEAM_DOMAIN": "Acme.cloudflareaccess.com",
    "ENS_UI_ACCESS_JWKS_FILE": "/state/jwks.json",
}


def test_valid_config_normalizes_and_derives_issuer():
    config = load_ui_config({**GOOD, "ENS_UI_ORIGIN": "https://ui.example.com/"})
    assert config.origin == "https://ui.example.com"
    assert config.issuer == "https://acme.cloudflareaccess.com"


@pytest.mark.parametrize("name", list(GOOD))
def test_missing_variable_is_named(name):
    env = {k: v for k, v in GOOD.items() if k != name}
    with pytest.raises(UiConfigError, match=name):
        load_ui_config(env)


@pytest.mark.parametrize(
    "origin",
    ["http://ui.example.com", "https://ui.example.com/app", "ui.example.com", "https://u:p@h.com"],
)
def test_origin_must_be_clean_https(origin):
    with pytest.raises(UiConfigError, match="ENS_UI_ORIGIN") as err:
        load_ui_config({**GOOD, "ENS_UI_ORIGIN": origin})
    assert origin not in str(err.value)


@pytest.mark.parametrize("domain", ["acme", "https://acme.cloudflareaccess.com", "a b.com"])
def test_team_domain_must_be_host(domain):
    with pytest.raises(UiConfigError, match="ENS_UI_ACCESS_TEAM_DOMAIN"):
        load_ui_config({**GOOD, "ENS_UI_ACCESS_TEAM_DOMAIN": domain})


def test_s3_workspace_is_refused(tmp_path, monkeypatch):
    settings = tmp_path / "settings.yaml"
    settings.write_text("workspace:\n  backend: s3\n  s3:\n    bucket: b\n")
    monkeypatch.setenv("ENS_SETTINGS", str(settings))
    for name, value in GOOD.items():
        monkeypatch.setenv(name, value)
    result = _serve()
    assert result.exit_code == 2
    assert "local" in result.output


def test_missing_config_exits_2_without_values(monkeypatch):
    for name in GOOD:
        monkeypatch.delenv(name, raising=False)
    result = _serve()
    assert result.exit_code == 2
    assert "ENS_UI_ORIGIN" in result.output
