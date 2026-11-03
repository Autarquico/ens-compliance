import json

import pytest
import yaml
from pydantic import BaseModel

from ens_evidence import __version__
from ens_evidence.connectors import Connector, ConnectorRegistry
from ens_evidence.connectors.cloudflare.connector import TOKEN_ENV
from ens_evidence.evidence import list_manifests
from ens_evidence.models import CollectionResult
from ens_evidence.models.enums import CollectionStatus
from ens_evidence.workspace import LocalStore

from .cli_support import CONFIG, run_collect, seed, stored_result, write_settings
from .fake_api import NOW, TOKEN, ZONE, FakeApi, denied, failure


@pytest.fixture
def api(api, install_connector) -> FakeApi:
    install_connector(transport=api.transport, clock=lambda: NOW, env={TOKEN_ENV: TOKEN})
    return api


def test_todo_recogido_sale_con_0_y_guarda_la_evidencia_del_collector(api, workspace):
    _, store = workspace

    result = run_collect(workspace)

    assert result.exit_code == 0, result.output
    (manifest,) = list_manifests(store).manifests
    assert manifest.source == "cloudflare"
    assert manifest.asset_id == "cf-taller"
    assert manifest.provenance.kind.value == "collector"
    assert manifest.provenance.submitted_by == "collector:cloudflare"
    assert manifest.collector.name == "cloudflare"
    assert manifest.collector.version == __version__
    assert manifest.collected_at == NOW
    assert f"Evidencia registrada: {manifest.id}" in result.output


def test_el_artefacto_guardado_es_el_collection_result_en_json_canonico(api, workspace):
    _, store = workspace

    run_collect(workspace)

    stored, data = stored_result(store)
    assert stored.collection_status is CollectionStatus.SUCCESS
    assert stored.source == "cloudflare"
    assert set(stored.data["sections"]) >= {"zones", "dns", "tunnels", "audit_logs"}
    assert json.dumps(json.loads(data), sort_keys=True, separators=(",", ":")).encode() == data


def test_imprime_el_estado_de_cada_seccion(api, workspace):
    result = run_collect(workspace)

    for name in ("zones", "dns", "tls", "waf", "access_applications", "tunnels", "audit_logs"):
        assert f"  {name}: SUCCESS" in result.output


def test_seccion_no_success_sale_con_3_y_guarda_la_evidencia(api, workspace):
    _, store = workspace
    api.set(f"/zones/{ZONE}/dns_records", denied())

    result = run_collect(workspace)

    assert result.exit_code == 3, result.output
    assert "  dns: PERMISSION_DENIED" in result.output
    stored, _ = stored_result(store)
    assert stored.collection_status is CollectionStatus.SUCCESS
    assert stored.data["sections"]["dns"]["status"] == "PERMISSION_DENIED"


def test_credencial_revocada_sale_con_1_y_guarda_la_evidencia_de_error(api, workspace):
    _, store = workspace
    api.set(r"/.*/tokens/verify", (401, failure(1000, "Invalid API Token")))

    result = run_collect(workspace)

    assert result.exit_code == 1, result.output
    assert "Error: HTTP 401" in result.output
    (manifest,) = list_manifests(store).manifests
    stored, _ = stored_result(store)
    assert stored.collection_status is CollectionStatus.ERROR
    assert manifest.provenance.submitted_by == "collector:cloudflare"
    assert TOKEN not in result.output


def test_sin_variable_de_token_sale_con_1_y_guarda_el_intento(workspace, install_connector):
    install_connector(env={})
    _, store = workspace

    result = run_collect(workspace)

    assert result.exit_code == 1
    assert "ENS_CLOUDFLARE_API_TOKEN" in result.output
    assert stored_result(store)[0].collection_status is CollectionStatus.ERROR


def test_revocacion_a_mitad_sale_con_1_y_guarda_error_global(api, workspace):
    _, store = workspace
    api.set(
        r"/.*/tokens/verify",
        [{"success": True, "errors": [], "result": {"status": "active"}}, (401, failure(1, "x"))],
    )

    result = run_collect(workspace)

    assert result.exit_code == 1
    assert stored_result(store)[0].collection_status is CollectionStatus.ERROR


def test_la_evidencia_guardada_y_la_salida_no_contienen_secretos(api, workspace):
    _, store = workspace

    result = run_collect(workspace)

    _, data = stored_result(store)
    for text in (data.decode(), result.output):
        assert "CANARY" not in text
        assert TOKEN not in text


def test_las_zonas_del_inventario_se_comparan_con_las_del_token(api, workspace):
    _, store = workspace
    assets = yaml.safe_load(store.read_text("scope/assets.yaml"))
    next(a for a in assets["assets"] if a["id"] == "cf-taller")["zones"].append("ausente.example")
    store.write_text("scope/assets.yaml", yaml.safe_dump(assets))

    run_collect(workspace)

    summary = stored_result(store)[0].data["sections"]["zones"]["summary"]
    assert summary["missing_from_token"] == ["ausente.example"]


def test_asset_id_inexistente_falla_sin_llamar_a_la_red_y_deja_un_error_de_prepare(api, workspace):
    _, store = workspace
    store.write_text("connectors/cloudflare.yaml", yaml.safe_dump({**CONFIG, "asset_id": "nope"}))

    result = run_collect(workspace)

    assert result.exit_code == 2
    assert "nope" in result.output
    assert api.calls == []
    stored, data = stored_result(store)
    assert stored.collection_status is CollectionStatus.ERROR
    assert (stored.data["phase"], stored.data["error"]) == (
        "prepare",
        "prepare_crashed:WorkspaceError",
    )
    assert b"debe ser" not in data


def test_asset_id_de_otro_tipo_falla(api, workspace):
    _, store = workspace
    store.write_text(
        "connectors/cloudflare.yaml", yaml.safe_dump({**CONFIG, "asset_id": "srv-app"})
    )

    result = run_collect(workspace)

    assert result.exit_code == 2
    assert "edge_network" in result.output
    assert api.calls == []
    stored, data = stored_result(store)
    assert stored.collection_status is CollectionStatus.ERROR
    assert (stored.data["phase"], stored.data["error"]) == (
        "prepare",
        "prepare_crashed:WorkspaceError",
    )
    assert b"edge_network" not in data


def test_configuracion_con_clave_de_credencial_falla_sin_llamar_a_la_red(api, workspace):
    _, store = workspace
    document = {**CONFIG, "api_token": "valor-CANARY"}
    store.write_text("connectors/cloudflare.yaml", yaml.safe_dump(document))

    result = run_collect(workspace)

    assert result.exit_code == 2
    assert "valor-CANARY" not in result.output
    assert api.calls == []


def test_sin_configuracion_indica_como_crearla(api, tmp_path):
    settings = write_settings(
        tmp_path, {"backend": "local", "local": {"path": str(tmp_path / "ws")}}
    )
    seed(LocalStore(tmp_path / "ws")).write_text("connectors/cloudflare.yaml", "")
    (tmp_path / "ws" / "connectors" / "cloudflare.yaml").unlink()

    result = run_collect((settings, None))

    assert result.exit_code == 2
    assert "connectors/cloudflare.yaml" in result.output
    assert api.calls == []


def test_ejecuciones_repetidas_guardan_una_evidencia_cada_una(api, workspace):
    _, store = workspace

    run_collect(workspace)
    run_collect(workspace)

    assert len(list_manifests(store).manifests) == 2


def test_token_no_ascii_sale_con_1_guarda_el_intento_y_no_lo_muestra(workspace, install_connector):
    _, store = workspace
    secreto = "tókén-CANARY"
    install_connector(env={TOKEN_ENV: secreto})

    result = run_collect(workspace)

    assert result.exit_code == 1, result.output
    assert TOKEN_ENV in result.output
    stored, data = stored_result(store)
    assert stored.collection_status is CollectionStatus.ERROR
    assert stored.data["sections"] == {}
    for text in (data.decode(), result.output):
        assert "CANARY" not in text


@pytest.mark.parametrize(
    ("status", "exit_code"),
    [
        (CollectionStatus.SUCCESS, 0),
        (CollectionStatus.PERMISSION_DENIED, 3),
        (CollectionStatus.NOT_SUPPORTED, 3),
        (CollectionStatus.ERROR, 1),
    ],
)
def test_estado_global_sin_secciones_determina_el_codigo_y_guarda_la_evidencia(
    status, exit_code, workspace, monkeypatch
):
    _, store = workspace

    class Config(BaseModel):
        asset_id: str
        account_id: str

    class GlobalStatusConnector(Connector):
        name = "cloudflare"
        config_model = Config

        def collect(self, config):
            return CollectionResult(
                source="cloudflare",
                collector=self.name,
                collected_at=NOW,
                asset_id=config.asset_id,
                data={},
                collection_status=status,
            )

    registry = ConnectorRegistry()
    registry.register(GlobalStatusConnector)
    monkeypatch.setattr("ens_evidence.cli.connector_registry", lambda: registry)

    result = run_collect(workspace)

    assert result.exit_code == exit_code, result.output
    assert stored_result(store)[0].collection_status is status
