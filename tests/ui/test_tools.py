"""Herramientas: panel de copias, asistente con descarga única y propuesta en la matriz."""

import io
import logging
import tarfile
from datetime import timedelta

import pytest
from applicability.helpers import LATER, decide_all_undetermined, prepared_workspace, uniform
from matrix.helpers import SIGNED_AT
from starlette.testclient import TestClient

from ens_evidence.applicability import approve_declaration, propose_applicability
from ens_evidence.checks.model import CheckInput, StoredCheckRun
from ens_evidence.checks.store import record_run
from ens_evidence.models.contracts import CheckResult
from ens_evidence.models.enums import CheckStatus
from ens_evidence.ui.app import create_ui_app
from ens_evidence.ui.config import COPIAS_HOST_ENV, UiConfigError, load_ui_config
from ens_evidence.ui.views.matrix import MATRIX_ROUTES

from .conftest import NOW, ORIGIN, Session, auth, jwks_for, snapshot_of

SHA = "a" * 64
WRITE_KEY = "WRITEKEY-sentinel-0001"
WRITE_SECRET = "WRITESECRET-sentinel-0002"
READ_KEY = "READKEY-sentinel-0003"
READ_SECRET = "READSECRET-sentinel-0004"
SENTINELS = (WRITE_KEY, WRITE_SECRET, READ_KEY, READ_SECRET)
FORM = {
    "variant": "caja",
    "id": "caja",
    "asset_id": "bk-diario",
    "endpoint": "https://cuenta.r2.cloudflarestorage.com",
    "bucket": "copias-ens",
    "prefix": "caja",
    "lock_days": "30",
    "cron": "0 2 * * *",
    "retention_days": "45",
    "max_backup_age_hours": "30",
    "max_check_age_days": "40",
    "restore_test_validity_days": "180",
    "min_check_read_data_percent": "5",
    "volumes": "data",
    "write_access_key_id": WRITE_KEY,
    "write_secret_access_key": WRITE_SECRET,
    "read_access_key_id": READ_KEY,
    "read_secret_access_key": READ_SECRET,
}


def record(store, *statuses: CheckStatus, complete: bool = True) -> None:
    results = [
        CheckResult(
            check_id="BACKUP-001",
            asset_id="bk-diario",
            status=s,
            evaluated_at=NOW - timedelta(hours=1),
            evidence_ids=["EV-20261108-aaaaaaaa"],
            reason="r",
        )
        for s in statuses
    ]
    run = StoredCheckRun(
        seq=1,
        connector="backups",
        engine_version="1",
        started_at=NOW - timedelta(hours=2),
        finished_at=NOW - timedelta(hours=1),
        complete=complete,
        scope_fingerprint=SHA,
        config_sha256=SHA,
        inputs=[CheckInput(evidence_id="EV-20261108-aaaaaaaa", manifest_sha256=SHA)],
        results=results,
    )
    record_run(store, run, NOW)


def members(body: bytes) -> dict[str, bytes]:
    with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as tar:
        return {m.name: tar.extractfile(m).read() for m in tar.getmembers() if m.isfile()}


def tail(text: str) -> str:
    return " ".join(text.split())


# --- panel ---------------------------------------------------------------------------------------


def test_panel_sin_evidencia_dice_sin_datos_y_propone_el_asistente(session):
    response = session.get("/herramientas")

    assert response.status_code == 200
    assert "Sin datos" in response.text
    assert 'href="/herramientas/copias"' in response.text
    assert "mp.info.6" in response.text


def test_panel_con_evidencia_al_dia_muestra_la_fecha(session, example_store):
    record(example_store, CheckStatus.PASS)

    response = session.get("/herramientas")

    assert "Al día" in response.text and "2026-11-08 11:00 UTC" in response.text


@pytest.mark.parametrize(
    "statuses,complete",
    [((CheckStatus.PASS, CheckStatus.FAIL), True), ((CheckStatus.PASS,), False)],
)
def test_panel_con_fallos_o_ejecucion_incompleta_dice_con_problemas(
    session, example_store, statuses, complete
):
    record(example_store, *statuses, complete=complete)

    assert "Con problemas" in session.get("/herramientas").text


def test_panel_abre_backrest_en_el_host_configurado(config, example_store, key, token):
    from dataclasses import replace

    from .conftest import Session, ui_client

    deployed = Session(
        ui_client(replace(config, copias_host="copias.example.org"), example_store, key), token
    )

    html = deployed.get("/herramientas").text

    assert 'href="https://copias.example.org"' in html and ">Abrir<" in html


def test_la_navegacion_enlaza_herramientas(session):
    assert 'href="/herramientas"' in session.get("/").text


def test_copias_host_se_lee_del_entorno():
    base = {
        "ENS_UI_ORIGIN": ORIGIN,
        "ENS_UI_ACCESS_AUD": "a",
        "ENS_UI_ACCESS_TEAM_DOMAIN": "acme.cloudflareaccess.com",
        "ENS_UI_ACCESS_JWKS_FILE": "/x",
    }
    assert load_ui_config(base).copias_host is None
    assert load_ui_config({**base, COPIAS_HOST_ENV: "Copias.Example.org"}).copias_host == (
        "copias.example.org"
    )
    with pytest.raises(UiConfigError) as caught:
        load_ui_config({**base, COPIAS_HOST_ENV: "https://nope/x"})
    assert "nope" not in str(caught.value)


# --- asistente -----------------------------------------------------------------------------------


def test_el_formulario_muestra_el_defecto_y_no_prellena_claves(session):
    html = session.get("/herramientas/copias").text

    assert 'name="min_check_read_data_percent" value="5"' in html
    assert "box.sh kit enable copias" in html and "primer acceso" in html
    assert 'type="password"' in html and 'value="ana@example.com"' not in html


def test_post_descarga_el_paquete_sin_escribir_en_el_workspace(session, example_store, caplog):
    caplog.set_level(logging.INFO, logger="ens_evidence.ui.audit")
    before = snapshot_of(example_store)

    response = session.post("/herramientas/copias", FORM)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-disposition"] == 'attachment; filename="copias-caja.tar.gz"'
    files = members(response.content)
    assert {"copias/config.json", "copias/app.env", "copias/MANIFEST"} <= set(files)
    assert snapshot_of(example_store) == before
    audit = [r.getMessage() for r in caplog.records if r.name == "ens_evidence.ui.audit"]
    assert len(audit) == 1 and "step=copias" in audit[0] and "action=paquete-caja" in audit[0]
    assert not any(s in audit[0] for s in SENTINELS)


def test_las_claves_estan_en_el_paquete_y_nada_mas(session, caplog):
    caplog.set_level(logging.DEBUG)

    response = session.post("/herramientas/copias", FORM)

    files = members(response.content)
    assert WRITE_SECRET.encode() in files["copias/config.json"]
    assert READ_SECRET.encode() in files["copias/app.env"]
    assert WRITE_SECRET.encode() not in files["copias/app.env"]
    assert not any(s in caplog.text for s in SENTINELS)


def test_otro_servidor_descarga_el_paquete_con_instrucciones(session):
    form = {k: v for k, v in FORM.items() if k != "volumes"}
    form |= {
        "variant": "servidor",
        "instance": "web01",
        "id": "web01",
        "host_paths": "/srv/datos\n/etc/app",
    }

    response = session.post("/herramientas/copias", form)

    assert response.status_code == 200
    assert "copias/INSTRUCCIONES.md" in members(response.content)


def test_otro_servidor_sin_rutas_o_con_volumenes_se_rechaza(session):
    form = FORM | {"variant": "servidor", "instance": "web01"}

    response = session.post("/herramientas/copias", form)

    assert response.status_code == 422
    assert "volúmenes de la caja" in tail(response.text)


def test_post_sin_origin_se_rechaza(raw_client, token):
    response = raw_client.post("/herramientas/copias", data=FORM, headers=auth(token))

    assert response.status_code == 403
    assert not any(s.encode() in response.content for s in SENTINELS)


def test_post_con_otro_origin_se_rechaza(raw_client, token):
    headers = {**auth(token), "Origin": "https://evil.example", "Sec-Fetch-Site": "same-origin"}

    assert raw_client.post("/herramientas/copias", data=FORM, headers=headers).status_code == 403


def test_ruta_prohibida_se_rechaza_con_mensaje_claro(session, caplog):
    caplog.set_level(logging.INFO, logger="ens_evidence.ui.audit")
    form = FORM | {"host_paths": "/proc/1"}

    response = session.post("/herramientas/copias", form)

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("text/html")
    assert "/proc no se puede copiar" in tail(response.text)
    assert not any("audit outcome" in r.getMessage() for r in caplog.records)


def test_el_error_no_devuelve_las_claves(session):
    form = FORM | {"retention_days": "mucho", "write_secret_access_key": WRITE_SECRET + " mal"}

    response = session.post("/herramientas/copias", form)

    assert response.status_code == 422
    assert not any(s in response.text for s in SENTINELS)
    assert "Escribe un número entero" in response.text
    assert "write_secret_access_key" in response.text  # el campo vuelve vacío y marcado


def test_claves_de_lectura_iguales_a_las_de_escritura_se_rechazan(session):
    form = FORM | {"read_access_key_id": WRITE_KEY, "read_secret_access_key": WRITE_SECRET}

    response = session.post("/herramientas/copias", form)

    assert response.status_code == 422
    assert "distintas" in tail(response.text)
    assert WRITE_SECRET not in response.text


def test_activo_que_no_es_de_copias_se_rechaza(session):
    response = session.post("/herramientas/copias", FORM | {"asset_id": "no-existe"})

    assert response.status_code == 422
    assert "activo de tipo copia" in tail(response.text)


def test_porcentaje_fuera_de_rango_se_rechaza_en_castellano(session):
    response = session.post("/herramientas/copias", FORM | {"min_check_read_data_percent": "101"})

    assert response.status_code == 422 and "entre 0 y 100" in response.text


def test_sin_elegir_que_copiar_se_rechaza(session):
    form = {k: v for k, v in FORM.items() if k != "volumes"}

    response = session.post("/herramientas/copias", form)

    assert response.status_code == 422 and "elige qué se copia" in tail(response.text)


def test_el_post_no_acepta_get_de_descarga(session):
    assert session.get("/herramientas/copias").headers["content-type"].startswith("text/html")


# --- matriz --------------------------------------------------------------------------------------


@pytest.fixture
def signed(tmp_path):
    store = prepared_workspace(tmp_path / "signed", uniform("MEDIO"))
    propose_applicability(store, LATER)
    decide_all_undetermined(store)
    approve_declaration(store, "Luis Pardo", SIGNED_AT)
    return store


@pytest.fixture
def web(config, signed, key, token) -> Session:
    app = create_ui_app(config, signed, lambda: NOW, lambda: jwks_for(key), routes=MATRIX_ROUTES)
    return Session(TestClient(app, base_url=ORIGIN), token)


def test_ficha_de_mp_info_6_propone_el_kit_mientras_no_haya_evidencia(web):
    html = web.get("/matriz/mp.info.6").text

    assert "kit de copias" in html and "Qué no cubre" in html
    assert "prueba de restauración" in tail(html) and 'href="/herramientas/copias"' in html


def test_ficha_de_mp_info_6_no_propone_el_kit_con_evidencia(web, signed):
    record(signed, CheckStatus.PASS)

    assert "kit de copias" not in web.get("/matriz/mp.info.6").text


def test_otra_ficha_no_propone_el_kit(web):
    assert "kit de copias" not in web.get("/matriz/op.pl.1").text


def test_las_paginas_nuevas_no_prometen_cumplimiento(session, web):
    import re

    forbidden = re.compile(r"certific|garantiza el cumplimiento|cumple el ENS", re.IGNORECASE)
    for path in ("/herramientas", "/herramientas/copias"):
        assert not forbidden.search(session.get(path).text), path
    assert not forbidden.search(web.get("/matriz/mp.info.6").text)


def test_el_aviso_del_bloqueo_sale_junto_al_formulario_y_tras_validar(session):
    assert "sigue ocupando espacio" in session.get("/herramientas/copias").text

    same_keys = {"read_access_key_id": WRITE_KEY, "read_secret_access_key": WRITE_SECRET}
    response = session.post("/herramientas/copias", FORM | same_keys)

    assert response.status_code == 422
    assert "Con bloqueo de objetos (30 días)" in response.text


def test_retencion_menor_que_el_bloqueo_no_se_rechaza(session):
    response = session.post("/herramientas/copias", FORM | {"retention_days": "10"})

    assert response.status_code == 200
