"""Panel de agentes: solo lectura, estados de alta, HOST-002 y ausencia de datos brutos."""

import re
from datetime import timedelta

import pytest
import yaml
from agent.protocol_support import (
    ENDPOINT_CONFIG,
    LAPTOP,
    LAPTOP_HOST,
    LAPTOP_UUID,
    OS_VERSION_ROW,
    SERVER,
)

from ens_evidence.agent.node_key import identity_sha256, normalize_host_identifier
from ens_evidence.agent.results import (
    Snapshot,
    contact_result,
    scheduled_result,
    write_result,
)

from .conftest import NOW, Session, seed_example_scope, ui_client

FORBIDDEN = re.compile(r"certific|garantiza el cumplimiento|cumple el ENS", re.IGNORECASE)
SENSITIVE_ROW = "MARCADOR-FILA-SENSIBLE"
ASSETS = f"""\
assets:
  - id: {LAPTOP}
    type: laptop
    deployment: deployed
    hostname: {LAPTOP_HOST}
    os: macOS 15
    owner: Ana Ruiz
    encrypted: true
    usage: Desarrollo
"""
HOST_ID = normalize_host_identifier(LAPTOP_UUID)


@pytest.fixture
def workspace(store):
    seed_example_scope(store)
    store.write_text("scope/assets.yaml", ASSETS)
    store.write_text("connectors/endpoint.yaml", yaml.safe_dump(ENDPOINT_CONFIG))
    return store


@pytest.fixture
def panel(config, workspace, key, token) -> Session:
    return Session(ui_client(config, workspace, key), token)


def register(store, **record):
    store.write_text("agents/registry.yaml", yaml.safe_dump({"agents": {LAPTOP: record}}))


def contact(store, *, generation=1, ago=timedelta(hours=1)):
    result = contact_result(LAPTOP, HOST_ID, "darwin", generation, now=NOW - ago)
    return write_result(store, result, now=NOW)


def snapshot(store, *, ago=timedelta(hours=1)):
    row = {**OS_VERSION_ROW, "name": SENSITIVE_ROW}
    result = scheduled_result(LAPTOP, HOST_ID, "darwin", [Snapshot("os_version", [row], NOW - ago)])
    return write_result(store, result, now=NOW)


def admitted(store, *, ago=timedelta(hours=30), generation=1):
    register(store, generation=generation, last_admission_at=(NOW - ago).isoformat())


def test_silent_agent_shows_host002_fail_with_reason_and_last_contact(panel, workspace):
    admitted(workspace)
    evidence = contact(workspace, ago=timedelta(hours=26))

    html = panel.get("/agentes").text
    detail = panel.get(f"/agentes/{LAPTOP}").text

    assert "Admitido" in html and "2026-11-07 10:00 UTC" in html
    assert "tone-bad" in html and "Falla" in html
    assert "agente sin contacto desde 2026-11-07 10:00 UTC" in detail
    assert evidence in detail
    assert "<form" not in html + detail and "<button" not in html + detail


def test_fresh_contact_and_snapshot_show_date_count_and_evidence(panel, workspace):
    admitted(workspace)
    contact(workspace)
    evidence = snapshot(workspace)

    detail = panel.get(f"/agentes/{LAPTOP}").text

    assert "HOST-002" in detail and "tone-ok" in detail
    row = detail[detail.index("os_version") :]
    assert "2026-11-08 11:00 UTC" in row and evidence in row


@pytest.mark.parametrize(
    ("setup", "label"),
    [
        (lambda s: None, "Sin alta"),
        (lambda s: register(s, generation=1, revoked_at=NOW.isoformat()), "Revocado"),
        (lambda s: s.write_text("agents/registry.yaml", "agents: [no"), "Registro ilegible"),
        (admitted, "Admitido"),
    ],
)
def test_admission_states(panel, workspace, setup, label):
    setup(workspace)
    html = panel.get("/agentes").text
    row = html[html.index(f">{LAPTOP}</a>") : html.index("</tr>", html.index(LAPTOP))]
    assert label in row


def test_contacts_of_other_generations_are_labeled_historical(panel, workspace):
    admitted(workspace, generation=2)
    old = contact(workspace, generation=1, ago=timedelta(hours=40))
    current = contact(workspace, generation=2)

    html = panel.get("/agentes").text
    detail = panel.get(f"/agentes/{LAPTOP}").text

    assert "2026-11-08 11:00 UTC" in html and "2026-11-07 20:00 UTC" not in html
    history = detail[detail.index("Contactos históricos") :]
    assert old in history and current not in history
    assert "Histórico, generación 1" in history


def test_unknown_asset_is_404(panel):
    assert panel.get("/agentes/fantasma").status_code == 404


def test_no_endpoint_config_shows_notice_not_error(config, store, key, token):
    page = Session(ui_client(config, seed_example_scope(store), key), token).get("/agentes")
    assert page.status_code == 200 and "No hay equipos inscritos" in page.text


def test_sensitive_values_never_reach_the_html(panel, workspace):
    admitted(workspace)
    contact(workspace)
    snapshot(workspace)
    forbidden = [SENSITIVE_ROW, LAPTOP_UUID, LAPTOP_UUID.upper(), identity_sha256(HOST_ID)]

    for path in ("/agentes", f"/agentes/{LAPTOP}"):
        html = panel.get(path).text
        for value in forbidden:
            assert value not in html
        assert not FORBIDDEN.search(html)


def test_panel_is_get_only_and_writes_nothing(panel, workspace):
    admitted(workspace)
    contact(workspace)
    before = {k: workspace.read_bytes(k) for k in workspace.list("")}

    assert panel.get("/agentes").status_code == 200
    assert panel.get(f"/agentes/{LAPTOP}").status_code == 200
    assert panel.post("/agentes", {}).status_code in (403, 404, 405)
    assert panel.post(f"/agentes/{LAPTOP}", {}).status_code in (403, 404, 405)

    assert {k: workspace.read_bytes(k) for k in workspace.list("")} == before
    assert SERVER in panel.get("/agentes").text


def test_without_endpoint_config_there_are_no_agents_and_no_warning(config, store, key, token):
    seed_example_scope(store)

    html = Session(ui_client(config, store, key), token).get("/agentes").text

    assert "No hay agentes configurados" in html
    assert "No se puede leer" not in html
    assert 'notice-warn" role="status"><p>No hay agentes' not in html


def test_unreadable_endpoint_config_stays_an_error(panel, workspace):
    workspace.write_text("connectors/endpoint.yaml", "hosts: [roto")

    html = panel.get("/agentes").text

    assert "No se puede leer connectors/endpoint.yaml" in html
    assert "No hay agentes configurados" not in html
