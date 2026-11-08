"""Los escenarios de docs/specs/osquery-server.md de punta a punta: la CLI `agent` y la app HTTP."""

import re

import pytest

from ens_evidence.cli import exclusive_cycle
from ens_evidence.evidence import list_manifests
from ens_evidence.evidence.verify import verify_all
from ens_evidence.workspace import load_settings

from . import test_cli_agent
from .conftest import SECRETS
from .protocol_support import (
    ENDPOINTS,
    LAPTOP,
    LAPTOP_UUID,
    OS_VERSION_ROW,
    Credentials,
    enroll_body,
    request_for,
    snapshot_event,
)

ens = test_cli_agent.ens
settings = test_cli_agent.settings  # la misma fixture, con el mismo workspace

NODE_INVALID = {"node_invalid": True}


@pytest.fixture(autouse=True)
def shared_secrets(settings, monkeypatch):
    """La CLI y la app del `world` comparten secretos, como en el servicio real."""
    monkeypatch.setenv("ENS_AGENT_ENROLL_KEY", SECRETS.enroll_key.decode())
    monkeypatch.setenv("ENS_AGENT_NODE_KEY_SECRET", SECRETS.node_key_secret.decode())


def admit(settings, hours: int = 24) -> str:
    result = ens(settings, "admit", "--asset", LAPTOP, "--by", "alex", "--hours", str(hours))
    assert result.exit_code == 0, result.output
    return result.stdout.strip()


def enroll_with(world, token: str):
    return world.post("enroll", enroll_body(Credentials(node_key="", enroll_token=token)))


def without_lock_files(listing: dict[str, str]) -> dict[str, str]:
    return {path: sha for path, sha in listing.items() if not path.endswith(".lock")}


def stored_evidence(world) -> list:
    return list_manifests(world.workspace).manifests


def test_alta_valida_el_host_inscrito_con_token_vigente_recibe_node_key(world, settings):
    token = admit(settings)

    response = enroll_with(world, token)

    assert response.status_code == 200
    node_key = response.json()["node_key"]
    assert response.json()["node_invalid"] is False
    config = world.post("config", {"node_key": node_key}).json()
    assert config["node_invalid"] is False and "os_version" in config["schedule"]
    assert LAPTOP in ens(settings, "hosts").stdout


def test_alta_invalida_token_erroneo_o_node_key_revocado_recibe_node_invalid_sin_escribir(
    world,
    settings,
):
    token = admit(settings)
    node_key = enroll_with(world, token).json()["node_key"]
    assert (
        ens(settings, "revoke", "--asset", LAPTOP, "--by", "alex", "--reason", "robado").exit_code
        == 0
    )
    stale = Credentials(node_key=node_key, enroll_token=token)
    wrong_token = Credentials(node_key=node_key, enroll_token="e1.9999999999." + "0" * 43)
    before = world.files()

    for credentials in (stale, wrong_token):
        for endpoint in ENDPOINTS:
            response = world.post(endpoint, request_for(endpoint, credentials))
            assert (response.status_code, response.json()) == (200, NODE_INVALID), endpoint

    assert world.files() == before
    assert stored_evidence(world) == []


def test_alta_invalida_node_key_de_otra_generacion_recibe_node_invalid_sin_escribir(
    world, settings
):
    first = enroll_with(world, admit(settings)).json()["node_key"]
    ens(settings, "revoke", "--asset", LAPTOP, "--by", "alex", "--reason", "robado")
    second_token = admit(settings)
    second = enroll_with(world, second_token).json()["node_key"]
    assert second != first
    before = world.files()

    for endpoint in ENDPOINTS[1:]:  # enroll no usa el node_key
        stale = Credentials(node_key=first, enroll_token=second_token)
        assert world.post(endpoint, request_for(endpoint, stale)).json() == NODE_INVALID

    assert world.files() == before
    assert world.post("config", {"node_key": second}).json()["node_invalid"] is False


def test_token_de_alta_caducado_recibe_node_invalid_sin_escribir(world, settings):
    token = admit(settings, hours=1)
    world.clock.advance(hours=1, seconds=1)
    before = world.files()

    response = enroll_with(world, token)

    assert (response.status_code, response.json()) == (200, NODE_INVALID)
    assert world.files() == before


def test_logs_de_estado_reciben_200_y_no_se_guarda_nada(world, settings):
    node_key = enroll_with(world, admit(settings)).json()["node_key"]
    before = world.files()
    status_lines = [
        {
            "severity": "0",
            "filename": "scheduler.cpp",
            "line": "76",
            "message": "Executing scheduled query os_version: SELECT * FROM os_version",
            "version": "5.14.1",
            "decorations": {"host_uuid": LAPTOP_UUID.upper()},
        }
    ]

    response = world.post("log", {"node_key": node_key, "log_type": "status", "data": status_lines})

    assert (response.status_code, response.json()) == (200, {"node_invalid": False})
    assert world.files() == before
    assert stored_evidence(world) == []


def test_lock_ocupado_recibe_503_sin_escribir_y_el_reintento_se_importa_una_sola_vez(
    world,
    settings,
):
    node_key = enroll_with(world, admit(settings)).json()["node_key"]
    results = {
        "node_key": node_key,
        "log_type": "result",
        "data": [snapshot_event("os_version", [OS_VERSION_ROW])],
    }
    before = world.files()

    with exclusive_cycle(load_settings(settings, allow_inside_product=True)):
        busy = world.post("log", results)
        assert busy.status_code == 503 and busy.headers["retry-after"]
        assert without_lock_files(world.files()) == without_lock_files(before)

    assert world.post("log", results).status_code == 200
    assert world.post("log", results).status_code == 200
    assert len(stored_evidence(world)) == 1
    assert verify_all(world.workspace).ok


def test_consulta_en_vivo_el_resultado_queda_disponible_para_la_cli(world, settings):
    node_key = enroll_with(world, admit(settings)).json()["node_key"]
    queued = ens(
        settings, "query", "run", "--asset", LAPTOP, "--query", "os_version", "--by", "alex"
    )
    assert queued.exit_code == 0, queued.output
    run_id = re.match(r"(live-[0-9a-f]{16}):", queued.stdout).group(1)

    picked = world.post("distributed/read", {"node_key": node_key}).json()["queries"]
    assert list(picked) == [run_id]
    answered = world.post(
        "distributed/write",
        {
            "node_key": node_key,
            "queries": {run_id: [OS_VERSION_ROW]},
            "statuses": {run_id: 0},
            "messages": {},
        },
    )

    assert answered.status_code == 200
    (evidence,) = stored_evidence(world)
    shown = ens(settings, "query", "show", run_id)
    assert shown.exit_code == 0, shown.output
    assert "contestada" in shown.stdout and evidence.id in shown.stdout
    assert run_id in ens(settings, "query", "list").stdout
