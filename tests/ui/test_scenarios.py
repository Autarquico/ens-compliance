"""Un test por escenario de docs/specs/web-ui-base.md y de docs/specs/web-ui-flujo.md."""

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

import ens_evidence.cli as cli
from ens_evidence.applicability import DeclarationState, declaration_status
from ens_evidence.assessment.dossier import scan_transitions
from ens_evidence.categorization import categorization_status
from ens_evidence.checks.source import StoredCheckResults
from ens_evidence.matrix import matrix_status
from ens_evidence.models.enums import Category, DossierState
from ens_evidence.orgdocs import DocumentState, all_statuses
from ens_evidence.preaudit.requests import request_prepare
from ens_evidence.risk import RiskState, risk_status
from ens_evidence.scheduling.scheduler import CycleExecutor
from ens_evidence.ui.store import WRITABLE_PREFIXES, UiStore, WriteNotAllowed
from ens_evidence.ui.wizard import STEPS
from ens_evidence.workspace import LocalStore

from . import test_equivalence as flows
from .conftest import (
    NOW,
    ORIGIN,
    WRITE_HEADERS,
    Session,
    auth,
    mint,
    snapshot_of,
    ui_client,
)
from .test_equivalence import fingerprints, run_cli, ui_script

web_flow = flows.web_flow  # la fixture de sesión: el flujo se recorre una vez para todos

RUNBOOK = Path(__file__).resolve().parents[2] / "docs" / "runbooks" / "interfaz-web.md"
GET_ROUTES = [
    "/", "/matriz", "/evidencias", "/expediente", "/preauditoria", "/asistente",
    *(f"/asistente/{s.slug}" for s in STEPS), "/static/ui.css",
]  # fmt: skip
POST_ROUTES = [f"/asistente/{s.slug}" for s in STEPS]


def unauthenticated_tokens(key, other_key) -> dict[str, str | None]:
    return {
        "sin cabecera": None,
        "caducado": mint(key, exp=1_700_000_000),
        "firmado por otra clave": mint(other_key),
        "alg none": mint(key, alg="none"),
        "basura": "no.es.unjwt",
    }


@pytest.fixture
def client(config, example_store, key):
    return ui_client(config, example_store, key)


def test_sin_access_no_se_entra(client, example_store, key, other_key):
    before = snapshot_of(example_store)
    form = {"name": "x", "description": "y", "accion": "aprobar", "approved_by": "x"}

    for label, token in unauthenticated_tokens(key, other_key).items():
        headers = {} if token is None else auth(token)
        for path in GET_ROUTES:
            response = client.get(path, headers=headers, follow_redirects=False)
            assert response.status_code == 401, (label, "GET", path)
        for path in POST_ROUTES:
            response = client.post(path, data=form, headers={**headers, **WRITE_HEADERS})
            assert response.status_code == 401, (label, "POST", path)

    assert snapshot_of(example_store) == before


def test_el_jwt_de_otra_audiencia_o_emisor_tampoco_entra(client, key, example_store):
    before = snapshot_of(example_store)
    for overrides in ({"aud": ["otra"]}, {"iss": "https://evil.cloudflareaccess.com"}):
        response = client.get("/", headers=auth(mint(key, **overrides)))
        assert response.status_code == 401
    assert snapshot_of(example_store) == before


def test_sin_certificados_nadie_entra(config, example_store, key):
    from starlette.testclient import TestClient

    from ens_evidence.ui.app import create_ui_app
    from ens_evidence.ui.routes import ui_routes

    from .conftest import NOW

    def no_keys():
        return {"keys": []}

    app = create_ui_app(config, example_store, lambda: NOW, no_keys, routes=ui_routes())
    response = TestClient(app, base_url=ORIGIN).get("/", headers=auth(mint(key)))
    assert response.status_code == 401


def test_mfa_exigido_por_access():
    text = RUNBOOK.read_text(encoding="utf-8")

    assert "## 3. Comprobar que Access exige MFA" in text
    section = text.split("## 3. Comprobar que Access exige MFA", 1)[1].split("\n## ", 1)[0]
    assert "exija MFA" in text.split("## 3.", 1)[0]
    assert "Authentication method" in section
    assert "Bypass" in section
    assert "segundo factor" in section
    assert "302" in section and "cloudflareaccess.com" in section


def test_alcance_y_categorizacion_equivalentes_a_la_cli(
    tmp_path, workspaces, config, key, token, monkeypatch
):
    cli_store, ui_store = workspaces
    run_cli(tmp_path, monkeypatch)
    ui_script(Session(ui_client(config, ui_store, key), token))

    keys = set(snapshot_of(cli_store))
    assert {"scope/approval.yaml", "scope/categorization.yaml"} <= keys
    assert "scope/categorization-approval.yaml" in keys
    assert keys == set(snapshot_of(ui_store))
    assert snapshot_of(cli_store) == snapshot_of(ui_store)  # bytes: la spec exige ficheros iguales
    assert fingerprints(cli_store) == fingerprints(ui_store)


HOSTILE_KEYS = [
    "connectors/github.yaml",
    "../connectors/x.yaml",
    "settings.yaml",
    "reports/r.md",
    "scope",
    "scope/../connectors/x.yaml",
    "/etc/passwd",
]


@pytest.mark.parametrize("key", HOSTILE_KEYS)
def test_la_ui_no_toca_la_configuracion(client, example_store, key):
    before = snapshot_of(example_store)
    store = client.app.state.store

    for write in (store.write_text, store.write_bytes, store.create_bytes):
        with pytest.raises(Exception) as caught:
            write(key, "x" if write is store.write_text else b"x")
        assert caught.type is WriteNotAllowed or caught.type.__name__ == "InvalidKey"

    assert snapshot_of(example_store) == before


def test_la_ui_no_toca_la_configuracion_por_ningun_handler(client, example_store, token):
    """Ids con forma de ruta en todos los pasos: nada fuera de la lista blanca cambia."""
    example_store.write_text("connectors/github.yaml", "token_env: X\n")
    before = snapshot_of(example_store)
    evil = "../connectors/github"
    form = {
        "name": evil, "description": evil, "asset_id": evil, "asset_type": "laptop",
        "service_id": evil, "service_name": evil, "service_assets": [evil],
        "dependency_id": evil, "asset": evil, "depends_on": evil, "info_id": evil,
        "included_assets": [evil], "included_services": [evil], "accion": "anadir",
        "approved_by": evil,
    }  # fmt: skip

    for path in POST_ROUTES:
        client.post(path, data=form, headers={**auth(token), **WRITE_HEADERS})

    after = snapshot_of(example_store)
    assert after["connectors/github.yaml"] == before["connectors/github.yaml"]
    outside = {k for k in set(after) | set(before) if k.split("/")[0] not in WRITABLE_PREFIXES}
    assert {k: after.get(k) for k in outside} == {k: before.get(k) for k in outside}
    assert not any(".." in k for k in after)
    assert isinstance(client.app.state.store, UiStore)


# --- docs/specs/web-ui-flujo.md ------------------------------------------------------------------


def flow_session(config, store, key, token) -> Session:
    return Session(ui_client(config, store, key), token)


def test_el_flujo_completo_por_la_web_termina_con_el_expediente_declarado(
    web_flow, config, key, token
):
    store = web_flow.store

    assert fingerprints(store)  # alcance aprobado y categorización vigente
    assert categorization_status(store, NOW).computation.category is Category.BASICA
    assert risk_status(store, NOW).state is RiskState.APPROVED
    assert declaration_status(store, NOW).state is DeclarationState.CURRENT
    assert {s.state for s in all_statuses(store)} == {DocumentState.APPROVED}
    rows = matrix_status(store, NOW, checks=StoredCheckResults(reference_now=NOW))[1]
    assert rows and all(r.row.review is not None for r in rows)
    assert scan_transitions(store, web_flow.dossier).dossier.state is DossierState.DECLARED
    page = flow_session(config, store, key, token).get("/expediente").text
    assert "La declaración ya está registrada" in page


def test_en_cada_estado_intermedio_la_pagina_de_estado_enlaza_a_un_paso_que_responde(web_flow):
    *intermediate, last = web_flow.milestones

    assert len(intermediate) >= 12
    for milestone in intermediate:
        assert milestone.url, milestone
        assert milestone.status == 200, milestone
    assert intermediate[0].url == "/asistente/sistema"
    assert {m.url.split("/")[1] for m in intermediate} == {"asistente", "matriz", "expediente"}
    assert last.name == "expediente: declare" and last.label


def test_la_web_no_da_por_buena_una_medida_sin_pruebas(web_flow, tmp_path, config, key, token):
    store = LocalStore(tmp_path / "copia")
    for name, data in snapshot_of(web_flow.store).items():
        store.write_bytes(name, data)
    ui = flow_session(config, store, key, token)
    ui.post("/matriz/org.1/desenlazar", {"kind": "org_document", "target": "politica-seguridad"})
    before = snapshot_of(store)

    response = ui.post(
        "/matriz/org.1/revisar",
        {
            "by": "Ana Ruiz",
            "role": "system_administrator",
            "assessment": "COMPLIANT",
            "conclusion": "Revisada.",
        },
    )

    assert response.status_code == 422
    assert "Cláusulas sin cubrir" in response.text
    for clause in ("org.1.1", "org.1.2", "org.1.3", "org.1.4", "org.1.5"):
        assert clause in response.text
    assert snapshot_of(store) == before


def test_scheduler_run_ejecuta_las_peticiones_de_la_ui_sin_proveedores_reales(
    tmp_path, monkeypatch
):
    store = LocalStore(tmp_path / "ws")
    settings = tmp_path / "settings.yaml"
    settings.write_text(
        yaml.safe_dump({"workspace": {"backend": "local", "local": {"path": str(tmp_path / "ws")}}})
    )
    built = {}

    class Recording:
        def __init__(self, clock, executor, *args, **kwargs):
            built.update(executor=executor, **kwargs)

        def run(self):
            built["ran"] = True

    monkeypatch.setattr(cli, "Scheduler", Recording)
    request = request_prepare(
        store, "20261108T120000000000Z-0123456789ab", by="Ana", email="ana@example.com", now=NOW
    )

    result = CliRunner().invoke(cli.app, ["--settings", str(settings), "scheduler", "run"])

    assert result.exit_code == 0, result.output
    requests = built["requests"]
    assert built["ran"] and isinstance(built["executor"], CycleExecutor)
    assert requests.__self__ is built["executor"]
    assert requests.__func__ is CycleExecutor.pending_requests
    assert requests(NOW) == [request.key]
