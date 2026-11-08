"""Subida de una evidencia manual: tope propio, nombre descartado y solo activos del alcance."""

import logging

import pytest
from starlette.testclient import TestClient

from ens_evidence.evidence import list_manifests
from ens_evidence.ui.app import create_ui_app
from ens_evidence.ui.forms import MAX_UPLOAD_BYTES, UPLOAD_ENVELOPE_BYTES
from ens_evidence.ui.views.evidence import EVIDENCE_ROUTES

from .conftest import NOW, ORIGIN, WRITE_HEADERS, auth, jwks_for, snapshot_of

ASSET = "lap-ana"
CONTENT = b"acta de la reunion de seguridad"
HOSTILE_NAME = "../../etc/passwd-secreto.txt"


@pytest.fixture
def client(config, example_store, key) -> TestClient:
    app = create_ui_app(
        config, example_store, lambda: NOW, lambda: jwks_for(key), routes=[*EVIDENCE_ROUTES]
    )
    return TestClient(app, base_url=ORIGIN)


@pytest.fixture
def web(client, token):

    def get(path: str):
        return client.get(path, headers=auth(token), follow_redirects=False)

    def post(data: dict, file: tuple[str, bytes] | None = ("acta.txt", CONTENT), **kwargs):
        files = {"file": (file[0], file[1], "text/plain")} if file else {"nada": (None, "")}
        return client.post(
            "/evidencias/nueva",
            data=data,
            files=files,
            headers={**auth(token), **WRITE_HEADERS},
            follow_redirects=False,
            **kwargs,
        )

    return get, post


def form(**overrides) -> dict:
    return {"asset_id": ASSET, "by": "Ana Ruiz", **overrides}


def test_formulario_ofrece_solo_activos_del_alcance_y_prellena_el_by(web):
    get, _ = web

    page = get("/evidencias/nueva").text

    assert f'<option value="{ASSET}"' in page
    assert "lap-personal" not in page
    assert 'name="by" value="ana@example.com"' in page
    assert 'enctype="multipart/form-data"' in page


def test_sube_una_evidencia_y_la_lista_la_muestra(web, example_store, caplog):
    get, post = web
    caplog.set_level(logging.INFO, logger="ens_evidence.ui.audit")

    response = post(form(collected_on="2026-11-01"))

    assert response.status_code == 303
    (manifest,) = list_manifests(example_store).manifests
    assert response.headers["location"] == f"/evidencias?subida={manifest.id}"
    assert (manifest.source, manifest.asset_id) == ("manual", ASSET)
    assert manifest.provenance.submitted_by == "Ana Ruiz"
    assert manifest.collected_at.date().isoformat() == "2026-11-01"
    assert example_store.read_bytes(manifest.artifact.key) == CONTENT
    assert "step=evidencias action=subir" in caplog.text
    assert "Ana Ruiz" not in caplog.text
    assert "Evidencia registrada" in get(response.headers["location"]).text


def test_sin_fecha_es_ahora(web, example_store):
    _, post = web

    post(form())

    (manifest,) = list_manifests(example_store).manifests
    assert manifest.collected_at == NOW


def test_el_nombre_del_fichero_se_descarta(web, example_store):
    _, post = web

    response = post(form(), file=(HOSTILE_NAME, CONTENT))

    assert response.status_code == 303
    assert all("passwd" not in k and "secreto" not in k for k in example_store.list(""))
    assert all(
        b"secreto" not in example_store.read_bytes(k) for k in example_store.list("evidence")
    )


@pytest.mark.parametrize(
    "data",
    [
        form(asset_id="lap-personal"),
        form(asset_id="no-existe"),
        form(by=""),
        form(collected_on="2099-01-01"),
        form(collected_on="ayer"),
    ],
)
def test_datos_invalidos_no_escriben(web, example_store, data):
    _, post = web
    before = snapshot_of(example_store)

    response = post(data)

    assert response.status_code == 422
    assert "No se ha guardado" in response.text
    assert snapshot_of(example_store) == before


def test_sin_fichero_o_vacio_no_escribe(web, example_store):
    _, post = web
    before = snapshot_of(example_store)

    assert post(form(), file=None).status_code == 422
    assert post(form(), file=("vacio.txt", b"")).status_code == 422
    assert snapshot_of(example_store) == before


def test_fichero_mayor_que_el_tope_se_rechaza_sin_escribir(web, example_store):
    _, post = web
    before = snapshot_of(example_store)

    response = post(form(), file=("grande.bin", b"x" * (MAX_UPLOAD_BYTES + 1)))

    assert response.status_code == 413
    assert snapshot_of(example_store) == before


def test_content_length_enorme_se_corta_antes_de_leer(client, token, example_store):
    before = snapshot_of(example_store)

    response = client.post(
        "/evidencias/nueva",
        content=b"x",
        headers={
            **auth(token),
            **WRITE_HEADERS,
            "content-type": "multipart/form-data; boundary=b",
            "content-length": str(MAX_UPLOAD_BYTES + UPLOAD_ENVELOPE_BYTES + 1),
        },
    )

    assert response.status_code == 413
    assert snapshot_of(example_store) == before


def test_cuerpo_en_trozos_sin_content_length_tambien_tiene_tope(client, token, example_store):
    before = snapshot_of(example_store)

    def chunks():
        for _ in range(7):
            yield b"x" * (1024 * 1024)

    response = client.post(
        "/evidencias/nueva",
        content=chunks(),
        headers={
            **auth(token),
            **WRITE_HEADERS,
            "content-type": "multipart/form-data; boundary=b",
        },
    )

    assert response.status_code == 413
    assert snapshot_of(example_store) == before


def test_cuerpo_que_no_es_multipart_es_400(client, token):
    response = client.post(
        "/evidencias/nueva",
        data={"asset_id": ASSET},
        headers={**auth(token), **WRITE_HEADERS},
    )

    assert response.status_code == 400
