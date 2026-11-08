"""Un test por escenario de docs/specs/web-ui-base.md."""

from pathlib import Path

import pytest

from ens_evidence.ui.store import WRITABLE_PREFIXES, UiStore, WriteNotAllowed
from ens_evidence.ui.wizard import STEPS

from .conftest import (
    ORIGIN,
    WRITE_HEADERS,
    auth,
    mint,
    snapshot_of,
    ui_client,
)
from .test_equivalence import assert_ui_and_cli_produce_the_same_workspace

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
    assert_ui_and_cli_produce_the_same_workspace(
        tmp_path, workspaces, config, key, token, monkeypatch
    )


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
