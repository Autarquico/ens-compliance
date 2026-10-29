import json
import sys

import pytest
import yaml

from ens_evidence import __version__
from ens_evidence.connectors.google_workspace import KEY_ENV
from ens_evidence.evidence import list_manifests
from ens_evidence.models.enums import CollectionStatus
from ens_evidence.workspace import LocalStore

from .cli_support import CONFIG, KEY, run_collect, seed, stored_result, write_settings
from .credentials import key_json, pem_canary
from .fake_api import (
    ACCESS_TOKEN,
    ASSIGNMENTS,
    CUSTOMER,
    NOW,
    TOKEN_EVENTS,
    USERS,
    FakeApi,
    denied,
    fixture,
    unauthorized,
)


@pytest.fixture
def api(api, install_connector) -> FakeApi:
    install_connector(transport=api.transport, clock=lambda: NOW, env={KEY_ENV: key_json()})
    return api


def test_todo_recogido_sale_con_0_y_guarda_la_evidencia(api, workspace):
    _, store = workspace

    result = run_collect(workspace)

    assert result.exit_code == 0, result.output
    (manifest,) = list_manifests(store).manifests
    assert manifest.source == "google-workspace"
    assert manifest.asset_id == "ws-identidad"
    assert manifest.provenance.kind.value == "collector"
    assert manifest.provenance.submitted_by == "collector:google-workspace"
    assert manifest.collector.name == "google-workspace"
    assert manifest.collector.version == __version__
    assert manifest.collected_at == NOW
    assert f"Evidencia registrada: {manifest.id}" in result.output
    sections = stored_result(store)[0].data["sections"]
    assert {n for n, s in sections.items() if s["status"] != "SUCCESS"} == set()


def test_el_artefacto_guardado_es_el_collection_result_en_json_canonico(api, workspace):
    _, store = workspace

    run_collect(workspace)

    stored, data = stored_result(store)
    assert stored.collection_status is CollectionStatus.SUCCESS
    assert stored.source == "google-workspace"
    assert set(stored.data["sections"]) == {
        "customer",
        "users",
        "admins",
        "oauth_token_events",
        "admin_events",
    }
    canonical = json.dumps(
        json.loads(data), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    assert canonical.encode() == data


def test_imprime_el_estado_de_cada_seccion(api, workspace):
    result = run_collect(workspace)

    for name in ("customer", "users", "admins", "oauth_token_events", "admin_events"):
        assert f"  {name}: SUCCESS" in result.output


def test_seccion_denegada_sale_con_3_y_guarda_la_evidencia(api, workspace):
    _, store = workspace
    api.set(TOKEN_EVENTS, denied())

    result = run_collect(workspace)

    assert result.exit_code == 3, result.output
    assert "  oauth_token_events: PERMISSION_DENIED" in result.output
    stored, _ = stored_result(store)
    assert stored.collection_status is CollectionStatus.SUCCESS
    assert stored.data["sections"]["oauth_token_events"]["status"] == "PERMISSION_DENIED"


def test_credencial_revocada_sale_con_1_y_guarda_la_evidencia_de_error(api, workspace):
    _, store = workspace
    api.set("/token", (400, {"error": "invalid_grant"}))

    result = run_collect(workspace)

    assert result.exit_code == 1, result.output
    assert "Error: HTTP 400: credencial revocada o no válida (invalid_grant)" in result.output
    (manifest,) = list_manifests(store).manifests
    stored, _ = stored_result(store)
    assert stored.collection_status is CollectionStatus.ERROR
    assert manifest.provenance.submitted_by == "collector:google-workspace"
    assert ACCESS_TOKEN not in result.output
    assert pem_canary() not in result.output


def test_revocacion_a_mitad_sale_con_1_y_guarda_error_global(api, workspace):
    _, store = workspace
    api.set(ASSIGNMENTS, unauthorized())

    result = run_collect(workspace)

    assert result.exit_code == 1
    stored, _ = stored_result(store)
    assert stored.collection_status is CollectionStatus.ERROR
    assert stored.data["error"] == "HTTP 401: credencial rechazada"


def test_revocacion_en_la_sonda_final_sale_con_1(api, workspace):
    _, store = workspace
    api.sequence(CUSTOMER, [fixture("customer"), unauthorized()])

    result = run_collect(workspace)

    assert result.exit_code == 1
    assert stored_result(store)[0].collection_status is CollectionStatus.ERROR


def test_sin_variable_de_clave_sale_con_1_y_guarda_el_intento(workspace, install_connector):
    install_connector(env={})
    _, store = workspace

    result = run_collect(workspace)

    assert result.exit_code == 1
    assert KEY_ENV in result.output
    assert stored_result(store)[0].collection_status is CollectionStatus.ERROR


def test_sin_el_extra_workspace_sale_con_1_e_indica_como_instalarlo(api, workspace, monkeypatch):
    monkeypatch.setitem(sys.modules, "cryptography.hazmat.primitives", None)

    result = run_collect(workspace)

    assert result.exit_code == 1
    assert "ens-evidence[workspace]" in result.output
    assert api.calls == []


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
    assert "identity" in result.output
    assert api.calls == []
    assert list_manifests(store).manifests == []


def test_customer_invalido_falla_sin_llamar_a_la_red(api, workspace):
    _, store = workspace
    store.write_text(KEY, yaml.safe_dump({**CONFIG, "customer": "otro"}))

    result = run_collect(workspace)

    assert result.exit_code == 2
    assert "customer" in result.output
    assert api.calls == []


def test_configuracion_con_clave_de_credencial_falla_sin_llamar_a_la_red(api, workspace):
    _, store = workspace
    store.write_text(KEY, yaml.safe_dump({**CONFIG, "private_key": "valor-CANARY"}))

    result = run_collect(workspace)

    assert result.exit_code == 2
    assert "valor-CANARY" not in result.output
    assert api.calls == []
    assert list_manifests(store).manifests == []


def test_configuracion_con_credencial_dentro_de_una_lista_falla(api, workspace):
    _, store = workspace
    store.write_text(KEY, yaml.safe_dump({**CONFIG, "domains": [{"password": "valor-CANARY"}]}))

    result = run_collect(workspace)

    assert result.exit_code == 2
    assert "valor-CANARY" not in result.output
    assert api.calls == []


def test_dominio_del_cliente_que_no_coincide_sale_con_1_y_no_lee_usuarios(api, workspace):
    _, store = workspace
    store.write_text(
        KEY,
        yaml.safe_dump(
            {
                **CONFIG,
                "domains": ["otra-empresa.example"],
                "admin_subject": "a@otra-empresa.example",
            }
        ),
    )

    result = run_collect(workspace)

    assert result.exit_code == 1
    assert stored_result(store)[0].collection_status is CollectionStatus.ERROR
    assert api.api_calls(USERS) == []


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
