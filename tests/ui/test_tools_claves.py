"""Herramientas: tarjeta y asistente del kit de claves, y su propuesta en la matriz."""

import io
import logging
import re
import tarfile
from dataclasses import replace
from datetime import timedelta

import pytest

from ens_evidence.checks.model import CheckInput, StoredCheckRun
from ens_evidence.checks.store import record_run
from ens_evidence.connectors.keystore import checks as keystore_checks
from ens_evidence.models.contracts import CheckResult
from ens_evidence.models.enums import CheckStatus
from ens_evidence.ui.config import CLAVES_HOST_ENV, load_ui_config
from ens_evidence.ui.views import help as help_view

from .conftest import NOW, ORIGIN, Session, snapshot_of, ui_client
from .test_tools import signed, tail, web  # noqa: F401  (fixtures reutilizadas)

SHA = "a" * 64
FORM = {
    "asset_id": "app-facturas",
    "key_shares": "5",
    "key_threshold": "3",
    "max_key_age_days": "365",
}
SEALED = "OpenBao está sellado: hacen falta 3 de 5 custodios para desbloquearlo"


def record(store, *results: tuple, complete: bool = True) -> None:
    run = StoredCheckRun(
        seq=1,
        connector="keystore",
        engine_version="1",
        started_at=NOW - timedelta(hours=2),
        finished_at=NOW - timedelta(hours=1),
        complete=complete,
        scope_fingerprint=SHA,
        config_sha256=SHA,
        inputs=[CheckInput(evidence_id="EV-20261108-aaaaaaaa", manifest_sha256=SHA)],
        results=[
            CheckResult(
                check_id=check_id,
                asset_id="app-facturas",
                status=status,
                evaluated_at=NOW - timedelta(hours=1),
                evidence_ids=["EV-20261108-aaaaaaaa"],
                reason=reason,
                reason_code=code[0] if code else None,
            )
            for check_id, status, reason, *code in results
        ],
    )
    record_run(store, run, NOW)


UNSEALED_OK = (keystore_checks.UNSEALED, CheckStatus.PASS, "OpenBao está inicializado y desellado")
UNSEALED_SEALED = (keystore_checks.UNSEALED, CheckStatus.FAIL, SEALED, keystore_checks.SEALED_CODE)
AUDIT_FAIL = (keystore_checks.AUDIT_ENABLED, CheckStatus.FAIL, "sin dispositivo de auditoría")


def card(html: str) -> str:
    return html.split('id="h-claves"', 1)[1]


def members(body: bytes) -> dict[str, bytes]:
    with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as tar:
        return {m.name: tar.extractfile(m).read() for m in tar.getmembers() if m.isfile()}


# --- tarjeta -------------------------------------------------------------------------------------


def test_sin_evidencia_la_tarjeta_dice_sin_datos_y_ofrece_el_asistente(session):
    html = card(session.get("/herramientas").text)

    assert "Sin datos" in html and 'href="/herramientas/claves"' in html
    assert ">Añadir las claves<" in html and "Preparar las claves" not in html
    assert "/ayuda/kit-claves" not in html


def test_sellado_sale_en_rojo_con_la_frase_y_el_enlace_al_runbook(session, example_store):
    record(example_store, UNSEALED_SEALED, AUDIT_FAIL)

    html = card(session.get("/herramientas").text)

    assert "tone-bad" in html and ">Sellado<" in html
    assert "hacen falta M de N custodios para desbloquearlo" in tail(html)
    assert 'href="/ayuda/kit-claves"' in html


def test_sellado_se_reconoce_por_el_codigo_y_no_por_la_frase(session, example_store):
    record(
        example_store,
        (
            keystore_checks.UNSEALED,
            CheckStatus.FAIL,
            "texto reescrito",
            keystore_checks.SEALED_CODE,
        ),
    )
    assert "Sellado" in card(session.get("/herramientas").text)


def test_fail_de_key_001_sin_el_codigo_de_sellado_no_es_sellado(session, example_store):
    record(example_store, (keystore_checks.UNSEALED, CheckStatus.FAIL, "OpenBao sellado y roto"))
    assert "Sellado" not in card(session.get("/herramientas").text)


def test_desellado_y_sano_dice_al_dia(session, example_store):
    record(example_store, UNSEALED_OK)

    html = card(session.get("/herramientas").text)

    assert "Al día" in html and "2026-11-08 11:00 UTC" in html


def test_con_un_fallo_distinto_del_sellado_dice_con_problemas(session, example_store):
    record(example_store, UNSEALED_OK, AUDIT_FAIL)

    html = card(session.get("/herramientas").text)

    assert "Con problemas" in html and "Sellado" not in html
    assert 'href="/ayuda/kit-claves"' in html


def test_sin_respuesta_dice_caida(session, example_store):
    record(example_store, (keystore_checks.UNSEALED, CheckStatus.ERROR, "sin red"))

    html = card(session.get("/herramientas").text)

    assert "Caída" in html and "no ha podido leer" in html


def test_las_copias_no_se_mezclan_con_el_estado_de_claves(session, example_store):
    record(example_store, UNSEALED_SEALED)

    html = session.get("/herramientas").text

    assert html.split('id="h-claves"')[0].count("Sin datos") == 1


def test_abrir_lleva_al_host_configurado(config, example_store, key, token):
    deployed = Session(
        ui_client(replace(config, kit_hosts={"claves": "claves.example.org"}), example_store, key),
        token,
    )

    html = card(deployed.get("/herramientas").text)

    assert 'href="https://claves.example.org"' in html and ">Preparar otro paquete<" in html


def test_claves_host_se_lee_del_entorno():
    base = {
        "ENS_UI_ORIGIN": ORIGIN,
        "ENS_UI_ACCESS_AUD": "a",
        "ENS_UI_ACCESS_TEAM_DOMAIN": "acme.cloudflareaccess.com",
        "ENS_UI_ACCESS_JWKS_FILE": "/x",
    }
    assert load_ui_config(base).kit_hosts.get("claves") is None
    assert load_ui_config({**base, CLAVES_HOST_ENV: "Claves.Example.org"}).kit_hosts["claves"] == (
        "claves.example.org"
    )


# --- asistente -----------------------------------------------------------------------------------


def test_el_aviso_de_irrecuperabilidad_va_antes_del_boton(session):
    html = tail(session.get("/herramientas/claves").text)

    warning = html.index("si se pierden más de N−M partes, los secretos son irrecuperables")
    assert warning < html.index("<button")
    assert "nunca se teclean en esta web" in html and "sin registro" in html
    assert "deploy/box.sh kit enable claves" in html and "deploy/box.sh kit claves init" in html
    assert 'value="app-facturas"' in html


def test_post_descarga_el_paquete_una_vez_sin_tocar_el_workspace(session, example_store, caplog):
    caplog.set_level(logging.INFO, logger="ens_evidence.ui.audit")
    before = snapshot_of(example_store)

    response = session.post("/herramientas/claves", FORM)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-disposition"] == 'attachment; filename="claves.tar.gz"'
    assert {"claves/init.json", "claves/keystore.yaml", "claves/MANIFEST"} <= set(
        members(response.content)
    )
    assert snapshot_of(example_store) == before
    audit = [r.getMessage() for r in caplog.records if r.name == "ens_evidence.ui.audit"]
    assert len(audit) == 1 and "step=claves" in audit[0] and "action=paquete" in audit[0]
    assert "app-facturas" not in audit[0] and "key_shares" not in audit[0]


def test_m_mayor_que_n_se_rechaza_con_422(session, caplog):
    caplog.set_level(logging.INFO, logger="ens_evidence.ui.audit")

    response = session.post(
        "/herramientas/claves", FORM | {"key_shares": "4", "key_threshold": "5"}
    )

    assert response.status_code == 422
    assert "M no puede ser mayor que N" in response.text
    assert not any("audit outcome" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize(
    "change",
    [
        {"key_shares": "2"},
        {"key_shares": "11"},
        {"key_threshold": "1"},
        {"key_threshold": "x"},
        {"max_key_age_days": "0"},
        {"asset_id": "no-existe"},
    ],
)
def test_entradas_fuera_de_rango_se_rechazan(session, change):
    response = session.post("/herramientas/claves", FORM | change)

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("text/html")


def test_post_con_otro_origin_se_rechaza(raw_client, token):
    headers = {
        "Cf-Access-Jwt-Assertion": token,
        "Origin": "https://evil.example",
        "Sec-Fetch-Site": "same-origin",
    }

    assert raw_client.post("/herramientas/claves", data=FORM, headers=headers).status_code == 403


# --- matriz --------------------------------------------------------------------------------------


def test_op_exp_10_propone_el_kit_sin_evidencia_y_sin_prometer_cumplimiento(web):  # noqa: F811
    html = web.get("/matriz/op.exp.10").text
    text = tail(html)

    assert "el kit de claves" in html and 'href="/herramientas/claves"' in html
    assert "op.exp.10.2" in text and "op.exp.10.3" in text and "R1" in text
    assert ">Añadir las claves<" in html and "cubierta en parte" in html
    assert not re.search(r"certific|garantiza el cumplimiento|cumple el ENS", html, re.I)


def test_op_exp_10_no_propone_el_kit_con_evidencia(web, signed):  # noqa: F811
    record(signed, UNSEALED_OK)

    assert "el kit de claves" not in web.get("/matriz/op.exp.10").text


def test_mp_info_6_sigue_proponiendo_copias_y_no_claves(web):  # noqa: F811
    html = web.get("/matriz/mp.info.6").text

    assert "kit de copias" in html and "kit de claves" not in html


def test_las_paginas_de_claves_no_prometen_cumplimiento(session, example_store):
    record(example_store, UNSEALED_SEALED)
    forbidden = re.compile(r"certific|garantiza el cumplimiento|cumple el ENS", re.IGNORECASE)

    for path in ("/herramientas", "/herramientas/claves"):
        assert not forbidden.search(session.get(path).text), path


# --- ayuda ---------------------------------------------------------------------------------------


def test_el_runbook_de_claves_da_404_limpio_si_el_fichero_no_existe(session, monkeypatch, tmp_path):
    monkeypatch.setattr(help_view, "RUNBOOKS_DIR", tmp_path)

    assert session.get("/ayuda/kit-claves").status_code == 404
