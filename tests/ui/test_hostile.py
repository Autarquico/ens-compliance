"""Pruebas hostiles sobre la UI: origen, XSS, cuerpos, rutas, cabeceras, identidad, escritura."""

import pytest
import yaml
from starlette.routing import Route
from starlette.testclient import TestClient

from ens_evidence.preaudit.records import scan_runs
from ens_evidence.preaudit.requests import list_requests
from ens_evidence.ui.app import create_ui_app
from ens_evidence.ui.forms import MAX_UPLOAD_BYTES
from ens_evidence.ui.routes import ui_routes
from ens_evidence.ui.store import UiStore, WriteNotAllowed
from ens_evidence.ui.wizard import STEPS

from .conftest import (
    NOW,
    ORIGIN,
    WRITE_HEADERS,
    Session,
    auth,
    jwks_for,
    snapshot_of,
    ui_client,
)
from .test_preaudit_actions import PREPARED_AT, consent
from .test_preaudit_actions import audited as audited  # noqa: PLC0414
from .test_preaudit_actions import prepared as prepared  # noqa: PLC0414
from .test_preaudit_actions import stages as stages  # noqa: PLC0414

VIEWS = ["/", "/matriz", "/evidencias", "/expediente", "/preauditoria"]
STEP_PATHS = [f"/asistente/{s.slug}" for s in STEPS]
ALL_PAGES = [*VIEWS, "/asistente", *STEP_PATHS]
XSS = "<script>alert(1)</script>"
IMG = '"><img src=x onerror=alert(2)>'
SECURITY_HEADERS = (
    "content-security-policy",
    "referrer-policy",
    "cache-control",
    "x-content-type-options",
)


@pytest.fixture
def client(config, example_store, key):
    return ui_client(config, example_store, key)


def post(client, token, path, data=None, headers=None, **kwargs):
    return client.post(
        path, data=data, headers={**auth(token), **WRITE_HEADERS, **(headers or {})}, **kwargs
    )


# --- origen ---------------------------------------------------------------------------------


@pytest.mark.parametrize("path", STEP_PATHS)
def test_post_de_otro_origen_es_403_y_no_escribe(client, example_store, token, path):
    before = snapshot_of(example_store)
    for headers in (
        {"Origin": "https://evil.example"},
        {"Origin": ORIGIN + ".evil.example"},
        {"Origin": "null"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},
    ):
        response = post(client, token, path, {"name": "x"}, headers)
        assert response.status_code == 403, headers
    assert snapshot_of(example_store) == before


@pytest.mark.parametrize("missing", ["Origin", "Sec-Fetch-Site"])
def test_post_sin_cabecera_de_origen_es_403(client, example_store, token, missing):
    before = snapshot_of(example_store)
    headers = {k: v for k, v in WRITE_HEADERS.items() if k != missing}

    response = client.post(
        "/asistente/sistema", data={"name": "x"}, headers={**auth(token), **headers}
    )

    assert response.status_code == 403
    assert snapshot_of(example_store) == before


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
def test_otros_metodos_que_escriben_tambien_exigen_origen(client, token, method):
    response = client.request(method, "/asistente/sistema", headers=auth(token))
    assert response.status_code == 403


# --- XSS ------------------------------------------------------------------------------------


def hostile_workspace(store) -> None:
    def edit(key, change):
        data = yaml.safe_load(store.read_text(key))
        change(data)
        store.write_text(key, yaml.safe_dump(data, sort_keys=False, allow_unicode=True))

    def system(d):
        d["name"] = XSS
        d["description"] = IMG

    def assets(d):
        for asset in d["assets"]:
            asset["hostname"] = XSS
            asset["owner"] = IMG
            asset["usage"] = XSS

    def services(d):
        for service in d["services"]:
            service["name"] = XSS

    def dependencies(d):
        for dep in d["dependencies"]:
            dep["purpose"] = IMG

    edit("scope/system.yaml", system)
    edit("scope/assets.yaml", assets)
    edit("scope/services.yaml", services)
    edit("scope/dependencies.yaml", dependencies)


def assert_escaped(body: str, where: str) -> None:
    assert XSS not in body, where
    assert "<img src=x" not in body, where
    assert "onerror=alert" not in body.replace("&", "").replace("amp;", "") or (
        "<img" not in body
    ), where


@pytest.mark.parametrize("path", ALL_PAGES)
def test_el_contenido_del_workspace_se_escapa_en_cada_vista(client, example_store, token, path):
    hostile_workspace(example_store)

    response = client.get(path, headers=auth(token), follow_redirects=True)

    assert_escaped(response.text, path)


def test_el_contenido_hostil_se_muestra_escapado_no_se_pierde(client, example_store, token):
    hostile_workspace(example_store)

    body = client.get("/asistente/sistema", headers=auth(token)).text

    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body


@pytest.mark.parametrize("path", STEP_PATHS)
def test_lo_que_se_envia_se_escapa_al_repintar_el_paso(client, token, path):
    form = {
        "name": XSS, "description": IMG, "asset_id": XSS, "asset_type": "laptop",
        "asset_os": IMG, "service_id": XSS, "service_name": IMG, "dependency_id": XSS,
        "purpose": IMG, "info_id": XSS, "approved_by": IMG, "accion": "anadir",
        "included_assets": [XSS], "included_services": [IMG], "service_assets": [XSS],
        "rating~information~datos~C~level": XSS, "rating~information~datos~C~justification": IMG,
        "valued_by~information~datos": XSS,
    }  # fmt: skip
    for htmx in (False, True):
        response = Session(client, token).post(path, form, htmx=htmx)
        assert_escaped(response.text, f"{path} htmx={htmx}")


def test_xss_en_la_query_se_escapa(client, token):
    response = client.get(
        "/asistente/activos", params={"tipo": XSS, "guardado": IMG}, headers=auth(token)
    )
    assert_escaped(response.text, "query")


# --- cuerpos --------------------------------------------------------------------------------


def test_cuerpo_enorme_es_413_y_no_escribe(client, example_store, token):
    before = snapshot_of(example_store)
    response = post(client, token, "/asistente/sistema", {"name": "a" * 300_000})
    assert response.status_code == 413
    assert snapshot_of(example_store) == before


def test_content_length_enorme_declarado_es_413(client, token):
    response = post(
        client,
        token,
        "/asistente/sistema",
        headers={"Content-Type": "application/x-www-form-urlencoded", "Content-Length": "9999999"},
        content=b"name=x",
    )
    assert response.status_code in (400, 413)


def test_demasiados_campos_se_rechaza(client, example_store, token):
    before = snapshot_of(example_store)
    body = "&".join(f"c{i}=1" for i in range(2000))
    response = post(
        client,
        token,
        "/asistente/sistema",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        content=body,
    )
    assert response.status_code in (400, 413)
    assert snapshot_of(example_store) == before


@pytest.mark.parametrize(
    ("content_type", "body"),
    [
        ("application/json", b'{"name": "x"}'),
        ("text/plain", b"name=x"),
        ("multipart/form-data; boundary=zz", b"--zz\r\nbasura sin cierre"),
        ("multipart/form-data", b"--zz--"),
        (
            "multipart/form-data; boundary=zz",
            (
                b'--zz\r\nContent-Disposition: form-data; name="f"; filename="a.txt"'
                b"\r\n\r\nx\r\n--zz--\r\n"
            ),
        ),
        ("application/x-www-form-urlencoded", b"\xff\xfe=%zz&&&=="),
    ],
)
def test_cuerpos_mal_formados_no_dan_500_ni_escriben(
    client, example_store, token, content_type, body
):
    before = snapshot_of(example_store)

    response = post(
        client, token, "/asistente/sistema", headers={"Content-Type": content_type}, content=body
    )

    assert response.status_code in (400, 413, 415, 422)
    assert snapshot_of(example_store) == before


def test_formulario_con_nul_y_unicode_raro_no_da_500(client, token):
    form = {"name": "a\x00b‮퟿", "description": "\n" * 10}
    assert post(client, token, "/asistente/sistema", form).status_code != 500


# --- rutas ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "slug",
    [
        "%2e%2e",
        "..%2fconnectors",
        "%2e%2e%2f%2e%2e%2fetc%2fpasswd",
        "nope",
        "sistema%00",
        "SISTEMA",
        "a" * 5000,
    ],
)
def test_parametros_de_ruta_hostiles_dan_404_sin_escribir(client, example_store, token, slug):
    before = snapshot_of(example_store)

    got = client.get(f"/asistente/{slug}", headers=auth(token), follow_redirects=False)
    posted = post(client, token, f"/asistente/{slug}", {"name": "x"})

    assert got.status_code in (404, 307, 308, 400)
    assert posted.status_code in (404, 307, 308, 400, 405)
    assert snapshot_of(example_store) == before


@pytest.mark.parametrize("path", ["/static/../ui/config.py", "/static/%2e%2e/config.py",
                                  "/static/..%2fconfig.py", "/static//etc/passwd"])  # fmt: skip
def test_static_no_sale_de_su_directorio(client, token, path):
    response = client.get(path, headers=auth(token))
    assert response.status_code in (400, 404)
    assert "UiConfig" not in response.text


# --- HX-Request y cabeceras ------------------------------------------------------------------


@pytest.mark.parametrize("path", ALL_PAGES)
def test_hx_request_no_se_salta_la_autenticacion(client, path):
    response = client.get(path, headers={"HX-Request": "true"}, follow_redirects=False)
    assert response.status_code == 401
    assert "<" not in response.text.replace('{"error"', "")


def test_post_htmx_sin_jwt_es_401(client, example_store):
    before = snapshot_of(example_store)
    response = client.post(
        "/asistente/sistema",
        data={"name": "x"},
        headers={**WRITE_HEADERS, "HX-Request": "true", "HX-Target": "paso"},
    )
    assert response.status_code == 401
    assert snapshot_of(example_store) == before


def assert_security_headers(response, where: str) -> None:
    for name in SECURITY_HEADERS:
        assert name in response.headers, (where, name)
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


@pytest.mark.parametrize("path", [*ALL_PAGES, "/static/ui.css", "/healthz"])
def test_cabeceras_de_seguridad_en_cada_respuesta_correcta(client, token, path):
    assert_security_headers(client.get(path, headers=auth(token), follow_redirects=False), path)


def test_cabeceras_de_seguridad_en_los_errores_de_la_app(client, token):
    responses = {
        "401": client.get("/"),
        "403": client.post("/asistente/sistema", data={}, headers=auth(token)),
        "404 ruta": client.get("/no-existe", headers=auth(token)),
        "404 paso": client.get("/asistente/nope", headers=auth(token)),
        "405": client.delete("/", headers={**auth(token), **WRITE_HEADERS}),
        "413": post(client, token, "/asistente/sistema", {"name": "a" * 300_000}),
        "400": post(
            client,
            token,
            "/asistente/sistema",
            headers={"Content-Type": "text/plain"},
            content=b"x",
        ),
        "422": Session(client, token).post("/asistente/sistema", {"name": ""}),
    }
    for label, response in responses.items():
        assert_security_headers(response, label)


def test_cabeceras_de_seguridad_en_un_500(config, example_store, key):
    async def boom(request):
        raise RuntimeError("fallo interno con datos sensibles")

    app = create_ui_app(
        config, example_store, lambda: NOW, lambda: jwks_for(key),
        routes=[*ui_routes(), Route("/boom", boom)],
    )  # fmt: skip
    from .conftest import mint

    response = TestClient(app, base_url=ORIGIN, raise_server_exceptions=False).get(
        "/boom", headers=auth(mint(key))
    )

    assert response.status_code == 500
    assert "fallo interno" not in response.text
    assert_security_headers(response, "500")


# --- identidad y «by» -----------------------------------------------------------------------


def test_cabeceras_de_identidad_falsas_no_cambian_quien_eres(client, token):
    spoof = {
        "Cf-Access-Authenticated-User-Email": "evil@example.com",
        "X-Forwarded-Email": "evil@example.com",
        "X-Forwarded-User": "evil@example.com",
        "X-Auth-Request-Email": "evil@example.com",
    }

    body = client.get("/asistente/aprobacion-alcance", headers={**auth(token), **spoof}).text

    assert "evil@example.com" not in body
    assert "ana@example.com" in body


def test_by_es_el_nombre_declarado_y_la_identidad_no_lo_sustituye(client, example_store, token):
    response = Session(client, token).post(
        "/asistente/aprobacion-alcance", {"accion": "aprobar", "approved_by": "Marta Gil"}
    )

    assert response.status_code == 303
    approval = yaml.safe_load(example_store.read_text("scope/approval.yaml"))
    assert approval["approved_by"] == "Marta Gil"
    assert "ana@example.com" not in example_store.read_text("scope/approval.yaml")


def test_by_vacio_o_solo_espacios_no_aprueba(client, example_store, token):
    before = snapshot_of(example_store)
    for by in ("", "   "):
        response = Session(client, token).post(
            "/asistente/aprobacion-alcance", {"accion": "aprobar", "approved_by": by}
        )
        assert response.status_code == 422
    assert snapshot_of(example_store) == before


def test_el_jwt_hostil_no_se_refleja_en_el_html(config, example_store, key):
    from .conftest import mint

    token = mint(key, email="<script>alert(3)</script>@example.com")
    body = ui_client(config, example_store, key).get("/", headers=auth(token)).text
    assert "<script>alert(3)" not in body


# --- las vistas no escriben ----------------------------------------------------------------


def test_las_vistas_de_solo_lectura_nunca_llaman_a_escribir(
    client, example_store, token, monkeypatch
):
    calls = []
    for name in ("write_text", "write_bytes", "create_bytes"):
        monkeypatch.setattr(UiStore, name, lambda self, *a, _n=name, **k: calls.append(_n))

    for path in [*VIEWS, "/asistente", *STEP_PATHS]:
        client.get(path, headers=auth(token), follow_redirects=True)

    assert calls == []


def test_404_y_405_responden_con_la_pagina_de_error(raw_client, token):
    client = raw_client
    not_found = client.get("/no-existe", headers=auth(token))
    not_allowed = client.delete("/", headers={**auth(token), **WRITE_HEADERS})

    assert not_found.status_code == 404 and not_found.headers["content-type"].startswith(
        "text/html"
    )
    assert not_allowed.status_code == 405 and not_allowed.headers["content-type"].startswith(
        "text/html"
    )
    assert "GET" in not_allowed.headers["allow"]


# --- el flujo completo: subida, consentimiento, lista blanca y origen en las rutas nuevas -------

ASSET = "lap-ana"
DOSSIER = "20261108T120000000000Z-0123456789ab"
FINDING = "a" * 64


def upload(client, token, content=b"acta", name="acta.txt", headers=None, **form):
    return client.post(
        "/evidencias/nueva",
        data={"asset_id": ASSET, "by": "Ana Ruiz", **form},
        files={"file": (name, content, "text/plain")},
        headers={**auth(token), **WRITE_HEADERS, **(headers or {})},
        follow_redirects=False,
    )


def test_una_subida_mayor_que_el_limite_se_rechaza_y_no_escribe_nada(client, example_store, token):
    before = snapshot_of(example_store)

    response = upload(client, token, b"x" * (MAX_UPLOAD_BYTES + 1))

    assert response.status_code == 413
    assert snapshot_of(example_store) == before


@pytest.mark.parametrize(
    "name",
    [
        "../../etc/passwd-secreto.txt",
        "..\\..\\windows\\secreto.txt",
        "/abs/secreto.txt",
        "carpeta/sub/secreto.txt",
        "%2e%2e/secreto.txt",
        "secreto" + "a" * 300 + ".txt",
    ],
)
def test_el_nombre_con_rutas_se_descarta_y_no_llega_al_workspace(
    client, example_store, token, name
):
    before = set(snapshot_of(example_store))

    response = upload(client, token, name=name)

    assert response.status_code == 303
    created = set(snapshot_of(example_store)) - before
    assert len(created) == 2
    assert all(k.startswith(("evidence/raw/", "evidence/manifests/")) for k in created)
    assert not any("secreto" in k for k in created)
    assert all(b"secreto" not in example_store.read_bytes(k) for k in created)


def test_una_subida_sin_origen_correcto_no_escribe(client, example_store, token):
    before = snapshot_of(example_store)

    for headers in ({"Origin": "https://evil.example"}, {"Sec-Fetch-Site": "cross-site"}):
        assert upload(client, token, headers=headers).status_code == 403

    assert snapshot_of(example_store) == before


@pytest.mark.parametrize(
    "change",
    [{"confirm_sha256": "0" * 64}, {"confirm_sha256": "A" * 64}, {"reviewed": None}],
)
def test_un_consentimiento_con_otra_huella_o_sin_marcar_no_crea_la_peticion(prepared, change):
    ui, store, _, done = prepared
    before = snapshot_of(store)
    data = {k: v for k, v in consent(done, **change).items() if v is not None}

    response = ui.post("/preauditoria/enviar", data)

    assert response.status_code == 422
    assert list_requests(store, PREPARED_AT) == []
    assert not scan_runs(store).records
    assert snapshot_of(store) == before


FORBIDDEN_KEYS = [
    "evidence/preaudits/runs/x.json",
    "evidence/preaudits/payloads/x.json",
    "evidence/preaudits/requests",
    "evidence/history/x.yaml",
    "evidence/x.yaml",
    "evidence",
    "org",
    "controls/../connectors/x.yaml",
    "evidence/manifests/../../settings.yaml",
    "evidence/preaudits/requests/../runs/x.json",
    "evidence/preaudits/dispositions/../../runs/x.json",
    "reports/schedule.yaml",
    "backups/x",
]


@pytest.mark.parametrize("key", FORBIDDEN_KEYS)
def test_la_lista_blanca_no_deja_escribir_fuera_de_lo_que_pide_el_flujo(client, example_store, key):
    before = snapshot_of(example_store)
    store = client.app.state.store

    for view in (store, store.recording()):
        for write in (view.write_text, view.write_bytes, view.create_bytes):
            with pytest.raises(Exception) as caught:
                write(key, "x" if write is view.write_text else b"x")
            assert caught.type is WriteNotAllowed or caught.type.__name__ == "InvalidKey"

    assert snapshot_of(example_store) == before


NEW_POSTS = [
    ("/matriz/org.1/responsable", {"owner_name": "Ana", "owner_role": "management"}),
    ("/matriz/org.1/enlazar", {"kind": "document", "target": "x", "covers": ["org.1.1"]}),
    ("/matriz/org.1/desenlazar", {"kind": "document", "target": "x"}),
    ("/matriz/org.1/revisar", {"by": "Ana", "role": "system_administrator"}),
    ("/expediente/generar", {}),
    (f"/expediente/{DOSSIER}/self-assess", {"by": "Ana", "role": "system_administrator"}),
    (f"/expediente/{DOSSIER}/analyze", {"by": "Ana", "conclusion": "x", "elevated_to": "Y"}),
    (f"/expediente/{DOSSIER}/declare", {"by": "Ana"}),
    (
        "/incidentes/nuevo",
        {"title": "Phishing", "detected_at": "2026-11-08T06:00", "by": "Ana Ruiz"},
    ),
    ("/incidentes/INC-2026-001/actualizar", {"by": "Ana Ruiz", "action": "Bloqueo"}),
    ("/incidentes/INC-2026-001/cerrar", {"by": "Ana Ruiz", "conclusion": "x"}),
    ("/incidentes/INC-2026-001/historial", {"by": "Ana Ruiz"}),
    ("/preauditoria/preparar", {"dossier": DOSSIER, "by": "Ana"}),
    ("/preauditoria/enviar", {"dossier": DOSSIER, "reviewed": "1", "by": "Ana"}),
    (f"/preauditoria/hallazgos/{FINDING}", {"decision": "dismissed", "reason": "x", "by": "Ana"}),
]
BAD_ORIGINS = [
    {"Origin": "https://evil.example"},
    {"Origin": ORIGIN + ".evil.example"},
    {"Origin": "null"},
    {"Sec-Fetch-Site": "cross-site"},
    {"Sec-Fetch-Site": "same-site"},
    {"Origin": None},
    {"Sec-Fetch-Site": None},
]


@pytest.mark.parametrize("path, data", NEW_POSTS, ids=[p for p, _ in NEW_POSTS])
def test_post_sin_origen_correcto_en_las_rutas_nuevas_es_403_y_no_escribe(
    client, example_store, token, path, data
):
    before = snapshot_of(example_store)
    headers_ok = {**auth(token), **WRITE_HEADERS}

    for change in BAD_ORIGINS:
        headers = {**headers_ok, **change}
        headers = {k: v for k, v in headers.items() if v is not None}
        assert client.post(path, data=data, headers=headers).status_code == 403, change

    assert snapshot_of(example_store) == before
    assert client.post(path, data=data, headers=headers_ok).status_code != 403
