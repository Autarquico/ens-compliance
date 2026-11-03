import pytest
import yaml
from pydantic import ValidationError

from ens_evidence.cli import app
from ens_evidence.connectors import ConnectorLoadError, ConnectorRegistry
from ens_evidence.connectors.cloudflare import CloudflareConfig
from ens_evidence.connectors.cloudflare.client import CloudflareClient, CloudflareError
from ens_evidence.connectors.cloudflare.connector import TOKEN_ENV
from ens_evidence.evidence import list_manifests

from .cli_support import run_collect, runner
from .fake_api import ACCOUNT, NOW, TOKEN, FakeApi


@pytest.mark.parametrize(
    "account_id",
    [
        "acc1",
        "",
        ACCOUNT.upper(),
        ACCOUNT + "0",
        ACCOUNT[:-1],
        f"{ACCOUNT[:-3]}/..",
        "../" + ACCOUNT,
    ],
)
def test_account_id_debe_ser_un_tag_de_cuenta(account_id):
    with pytest.raises(ValidationError):
        CloudflareConfig(asset_id="cf-taller", account_id=account_id)


@pytest.mark.parametrize("path", ["/zones/../accounts", "/zones/a%2Fb/dns_records", "/zones/a?x=1"])
def test_el_cliente_rechaza_rutas_con_identificadores_no_validos(path):
    api = FakeApi()
    with CloudflareClient(TOKEN, transport=api.transport) as client:
        with pytest.raises(CloudflareError, match="identificador"):
            client.get(path)
    assert api.calls == []


def test_fuente_desconocida_sale_con_2_y_lista_los_conectores(workspace):
    settings, store = workspace

    result = runner.invoke(app, ["--settings", str(settings), "collect", "nadie"])

    assert result.exit_code == 2
    assert "nadie" in result.output
    assert "cloudflare" in result.output
    assert list_manifests(store).manifests == []


def test_collect_resuelve_el_conector_por_el_registro(workspace, install_connector):
    api = FakeApi()
    install_connector(transport=api.transport, clock=lambda: NOW, env={TOKEN_ENV: TOKEN})

    result = run_collect(workspace)

    assert result.exit_code == 0, result.output
    assert api.calls


def test_sin_entry_point_el_conector_no_se_encuentra(workspace, monkeypatch):
    monkeypatch.setattr("ens_evidence.cli.connector_registry", ConnectorRegistry)

    result = run_collect(workspace)

    assert result.exit_code == 2
    assert "cloudflare" in result.output


def test_config_yaml_con_account_id_invalido_sale_con_2_sin_red(workspace):
    _, store = workspace
    store.write_text(
        "connectors/cloudflare.yaml",
        yaml.safe_dump({"asset_id": "cf-taller", "account_id": "../x"}),
    )

    result = run_collect(workspace)

    assert result.exit_code == 2
    assert "account_id" in result.output


def test_plugin_defectuoso_sale_con_2_sin_traceback_ni_texto_del_plugin(workspace, monkeypatch):
    def broken():
        raise ConnectorLoadError("cannot load 'x': PLUGIN-CANARY")

    monkeypatch.setattr("ens_evidence.cli.connector_registry", broken)

    result = run_collect(workspace)

    assert result.exit_code == 2
    assert "plugin" in result.output
    assert "PLUGIN-CANARY" not in result.output
    assert "Traceback" not in result.output
