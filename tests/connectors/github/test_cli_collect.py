import json

import pytest
import yaml

from ens_evidence import __version__
from ens_evidence.connectors.github.connector import TOKEN_ENV
from ens_evidence.evidence import list_manifests
from ens_evidence.models.enums import CollectionStatus
from ens_evidence.workspace import LocalStore

from .cli_support import CONFIG, KEY, run_collect, seed, stored_result, write_settings
from .fake_api import FACTURAS, NOW, REPO, TOKEN, FakeApi, denied, message


@pytest.fixture
def api(api, install_connector) -> FakeApi:
    install_connector(transport=api.transport, clock=lambda: NOW, env={TOKEN_ENV: TOKEN})
    return api


def test_todo_recogido_sale_con_0_y_guarda_la_evidencia(api, workspace):
    _, store = workspace

    result = run_collect(workspace)

    assert result.exit_code == 0, result.output
    (manifest,) = list_manifests(store).manifests
    assert manifest.source == "github"
    assert manifest.asset_id == "gh-taller"
    assert manifest.provenance.kind.value == "collector"
    assert manifest.provenance.submitted_by == "collector:github"
    assert manifest.collector.name == "github"
    assert manifest.collector.version == __version__
    assert manifest.collected_at == NOW
    assert f"Evidencia registrada: {manifest.id}" in result.output
    sections = stored_result(store)[0].data["sections"]
    assert {n for n, s in sections.items() if s["status"] != "SUCCESS"} == set()
    assert "personal_access_tokens" not in sections


def test_el_artefacto_guardado_es_el_collection_result_en_json_canonico(api, workspace):
    _, store = workspace

    run_collect(workspace)

    stored, data = stored_result(store)
    assert stored.collection_status is CollectionStatus.SUCCESS
    assert stored.source == "github"
    assert set(stored.data["sections"]) >= {"organization", "repositories", "branch_rules"}
    canonical = json.dumps(
        json.loads(data), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    assert canonical.encode() == data


def test_imprime_el_estado_de_cada_seccion(api, workspace):
    result = run_collect(workspace)

    for name in ("organization", "repositories", "branch_rules", "dependabot", "deploy_keys"):
        assert f"  {name}: SUCCESS" in result.output
    assert "personal_access_tokens" not in result.output


def test_seccion_denegada_sale_con_3_y_guarda_la_evidencia(api, workspace):
    _, store = workspace
    api.set(rf"{REPO}/keys", denied())

    result = run_collect(workspace)

    assert result.exit_code == 3, result.output
    assert "  deploy_keys: PERMISSION_DENIED" in result.output
    stored, _ = stored_result(store)
    assert stored.collection_status is CollectionStatus.SUCCESS
    assert stored.data["sections"]["deploy_keys"]["status"] == "PERMISSION_DENIED"


def test_credencial_revocada_sale_con_1_y_guarda_la_evidencia_de_error(api, workspace):
    _, store = workspace
    api.set("/user", (401, message("Bad credentials")))

    result = run_collect(workspace)

    assert result.exit_code == 1, result.output
    assert "Error: HTTP 401" in result.output
    (manifest,) = list_manifests(store).manifests
    stored, _ = stored_result(store)
    assert stored.collection_status is CollectionStatus.ERROR
    assert manifest.provenance.submitted_by == "collector:github"
    assert TOKEN not in result.output


def test_revocacion_a_mitad_sale_con_1_y_guarda_error_global(api, workspace):
    _, store = workspace
    api.set(rf"{FACTURAS}/keys", (401, message("Bad credentials")))

    result = run_collect(workspace)

    assert result.exit_code == 1
    assert stored_result(store)[0].collection_status is CollectionStatus.ERROR


def test_sin_variable_de_token_sale_con_1_y_guarda_el_intento(workspace, install_connector):
    install_connector(env={})
    _, store = workspace

    result = run_collect(workspace)

    assert result.exit_code == 1
    assert TOKEN_ENV in result.output
    assert stored_result(store)[0].collection_status is CollectionStatus.ERROR


def test_la_evidencia_guardada_y_la_salida_no_contienen_secretos_ni_cuerpos_de_error(
    api, workspace
):
    _, store = workspace
    api.set(rf"{REPO}/collaborators", (500, message(f"boom-CANARY {TOKEN}")))

    result = run_collect(workspace)

    _, data = stored_result(store)
    for text in (data.decode(), result.output):
        assert "CANARY" not in text
        assert TOKEN not in text


def test_los_repos_del_inventario_se_comparan_con_los_del_token(api, workspace):
    _, store = workspace
    assets = yaml.safe_load(store.read_text("scope/assets.yaml"))
    next(a for a in assets["assets"] if a["id"] == "gh-taller")["repos"].append("ausente")
    store.write_text("scope/assets.yaml", yaml.safe_dump(assets))

    run_collect(workspace)

    summary = stored_result(store)[0].data["sections"]["repositories"]["summary"]
    assert summary["missing_from_token"] == ["ausente"]
    assert summary["not_in_inventory"] == []


def test_asset_id_inexistente_falla_sin_llamar_a_la_red_ni_guardar_evidencia(api, workspace):
    _, store = workspace
    store.write_text(KEY, yaml.safe_dump({**CONFIG, "asset_id": "nope"}))

    result = run_collect(workspace)

    assert result.exit_code == 2
    assert "nope" in result.output
    assert api.calls == []
    assert list_manifests(store).manifests == []


def test_asset_id_de_otro_tipo_falla(api, workspace):
    _, store = workspace
    store.write_text(KEY, yaml.safe_dump({**CONFIG, "asset_id": "srv-app"}))

    result = run_collect(workspace)

    assert result.exit_code == 2
    assert "code_hosting" in result.output
    assert api.calls == []
    assert list_manifests(store).manifests == []


def test_configuracion_con_clave_de_credencial_falla_sin_llamar_a_la_red(api, workspace):
    _, store = workspace
    store.write_text(KEY, yaml.safe_dump({**CONFIG, "token": "valor-CANARY"}))

    result = run_collect(workspace)

    assert result.exit_code == 2
    assert "valor-CANARY" not in result.output
    assert api.calls == []
    assert list_manifests(store).manifests == []


def test_configuracion_con_credencial_dentro_de_una_lista_falla(api, workspace):
    _, store = workspace
    store.write_text(KEY, yaml.safe_dump({**CONFIG, "repos": [{"password": "valor-CANARY"}]}))

    result = run_collect(workspace)

    assert result.exit_code == 2
    assert "valor-CANARY" not in result.output
    assert api.calls == []


def test_sin_configuracion_indica_como_crearla(api, tmp_path):
    settings = write_settings(
        tmp_path, {"backend": "local", "local": {"path": str(tmp_path / "ws")}}
    )
    store = seed(LocalStore(tmp_path / "ws"))
    (tmp_path / "ws" / KEY).unlink()

    result = run_collect((settings, store))

    assert result.exit_code == 2
    assert KEY in result.output
    assert api.calls == []


def test_ejecuciones_repetidas_guardan_una_evidencia_cada_una(api, workspace):
    _, store = workspace

    run_collect(workspace)
    run_collect(workspace)

    assert len(list_manifests(store).manifests) == 2
