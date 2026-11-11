"""Herramientas: tarjeta y asistente del kit de contraseñas, y su propuesta en la matriz."""

import io
import logging
import re
import tarfile
from dataclasses import replace
from datetime import timedelta

import pytest
import yaml

from ens_evidence.checks.model import CheckInput, StoredCheckRun
from ens_evidence.checks.store import record_run
from ens_evidence.connectors.passwords import checks as passwords_checks
from ens_evidence.models.contracts import CheckResult
from ens_evidence.models.enums import CheckStatus
from ens_evidence.ui.config import CONTRASENAS_HOST_ENV, load_ui_config

from .conftest import NOW, ORIGIN, Session, snapshot_of, ui_client
from .test_tools import tail

SHA = "a" * 64
FORM = {"asset_id": "app-facturas", "inactive_days": "90", "host": "Contrasenas.Example.org"}
FORBIDDEN = re.compile(r"certific|garantiza el cumplimiento|cumple el ENS", re.IGNORECASE)


def record(store, *results: tuple, complete: bool = True) -> None:
    run = StoredCheckRun(
        seq=1,
        connector="passwords",
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
            )
            for check_id, status, reason in results
        ],
    )
    record_run(store, run, NOW)


SECOND_OK = (passwords_checks.SECOND_FACTOR, CheckStatus.PASS, "todas con segundo factor")
SECOND_FAIL = (passwords_checks.SECOND_FACTOR, CheckStatus.FAIL, "una cuenta sin segundo factor")


def card(html: str) -> str:
    return html.split('id="h-contrasenas"', 1)[1]


def members(body: bytes) -> dict[str, bytes]:
    with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as tar:
        return {m.name: tar.extractfile(m).read() for m in tar.getmembers() if m.isfile()}


# --- tarjeta -------------------------------------------------------------------------------------


def test_sin_evidencia_la_tarjeta_dice_sin_datos_y_ofrece_el_asistente(session):
    html = card(session.get("/herramientas").text)

    assert "Sin datos" in html and 'href="/herramientas/contrasenas"' in html
    assert ">Añadir las contraseñas<" in html
    assert "/ayuda/kit-contrasenas" not in html


def test_al_dia_con_el_resumen_sano(session, example_store):
    record(example_store, SECOND_OK)

    html = card(session.get("/herramientas").text)

    assert "Al día" in html and "2026-11-08 11:00 UTC" in html


def test_con_un_fallo_dice_con_problemas_y_enlaza_al_runbook(session, example_store):
    record(example_store, SECOND_OK, SECOND_FAIL)

    html = card(session.get("/herramientas").text)

    assert "Con problemas" in html and "segundo factor" in tail(html)
    assert 'href="/ayuda/kit-contrasenas"' in html


def test_sin_respuesta_dice_caida(session, example_store):
    record(example_store, (passwords_checks.SECOND_FACTOR, CheckStatus.ERROR, "export_stale"))

    html = card(session.get("/herramientas").text)

    assert "Caída" in html and "demasiado viejo" in html


def test_el_estado_de_contrasenas_no_se_mezcla_con_el_de_claves(session, example_store):
    record(example_store, SECOND_FAIL)

    html = session.get("/herramientas").text

    assert "Con problemas" not in html.split('id="h-contrasenas"')[0]


def test_abrir_lleva_al_host_configurado(config, example_store, key, token):
    hosts = {"contrasenas": "contrasenas.example.org"}
    deployed = Session(ui_client(replace(config, kit_hosts=hosts), example_store, key), token)

    html = card(deployed.get("/herramientas").text)

    assert 'href="https://contrasenas.example.org"' in html and ">Preparar otro paquete<" in html


def test_contrasenas_host_se_lee_del_entorno():
    base = {
        "ENS_UI_ORIGIN": ORIGIN,
        "ENS_UI_ACCESS_AUD": "a",
        "ENS_UI_ACCESS_TEAM_DOMAIN": "acme.cloudflareaccess.com",
        "ENS_UI_ACCESS_JWKS_FILE": "/x",
    }
    assert CONTRASENAS_HOST_ENV == "ENS_CONTRASENAS_HOST"
    assert load_ui_config(base).kit_hosts.get("contrasenas") is None
    loaded = load_ui_config({**base, CONTRASENAS_HOST_ENV: "Pw.Example.org"})
    assert loaded.kit_hosts["contrasenas"] == "pw.example.org"


# --- asistente -----------------------------------------------------------------------------------


def test_el_asistente_explica_el_alcance_y_da_los_comandos(session):
    html = tail(session.get("/herramientas/contrasenas").text)

    assert "deploy/box.sh kit enable contrasenas" in html
    assert "ENS_CONTRASENAS_HOST=" in html and "deploy/ui.env" in html
    assert "deploy/box.sh kit contrasenas alta" in html
    assert "deploy/box.sh kit contrasenas cerrar-alta" in html
    assert html.index("ENS_CONTRASENAS_HOST=") < html.index("kit enable contrasenas")
    assert html.index("kit enable contrasenas") < html.index("kit contrasenas alta")
    assert html.index("kit contrasenas alta") < html.index("/admin") < html.index("cerrar-alta")
    assert "una sola vez" in html and "primer propietario" in html
    assert "cuando todas estén dadas de alta" not in html.lower()
    assert 'href="/ayuda/kit-contrasenas"' in html
    assert "no la suspende" in html and "no lee los cofres" in html
    assert "política de tu organización" in html
    assert 'value="app-facturas"' in html


def test_post_descarga_el_paquete_sin_tocar_el_workspace(session, example_store, caplog):
    caplog.set_level(logging.INFO, logger="ens_evidence.ui.audit")
    before = snapshot_of(example_store)

    response = session.post("/herramientas/contrasenas", FORM)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-disposition"] == 'attachment; filename="contrasenas.tar.gz"'
    files = members(response.content)
    assert {"contrasenas/passwords.yaml", "contrasenas/MANIFEST"} <= set(files)
    config = yaml.safe_load(files["contrasenas/passwords.yaml"])
    assert config["asset_id"] == "app-facturas" and config["inactive_days"] == 90
    assert b"example.org" not in b"".join(files.values()).lower()
    assert snapshot_of(example_store) == before
    audit = [r.getMessage() for r in caplog.records if r.name == "ens_evidence.ui.audit"]
    assert len(audit) == 1 and "step=contrasenas" in audit[0] and "action=paquete" in audit[0]
    assert "app-facturas" not in audit[0] and "example.org" not in audit[0]


@pytest.mark.parametrize(
    "change",
    [
        {"inactive_days": "0"},
        {"inactive_days": "x"},
        {"inactive_days": "-5"},
        {"inactive_days": ""},
        {"asset_id": "no-existe"},
        {"host": "https://pw.example.org"},
        {"host": "pw.example.org:8443"},
        {"host": ""},
    ],
)
def test_entradas_invalidas_se_rechazan_con_422(session, change):
    response = session.post("/herramientas/contrasenas", FORM | change)

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("text/html")
    assert "No se ha generado el paquete" in response.text


def test_el_error_devuelve_lo_escrito_escapado(session):
    response = session.post("/herramientas/contrasenas", FORM | {"host": '"><script>x</script>'})

    assert response.status_code == 422
    assert "<script>x</script>" not in response.text


def test_post_con_otro_origin_se_rechaza(raw_client, token):
    headers = {
        "Cf-Access-Jwt-Assertion": token,
        "Origin": "https://evil.example",
        "Sec-Fetch-Site": "same-origin",
    }

    response = raw_client.post("/herramientas/contrasenas", data=FORM, headers=headers)

    assert response.status_code == 403


# --- matriz --------------------------------------------------------------------------------------


def test_op_acc_6_propone_el_kit_sin_cobertura_ni_promesas(web):
    html = web.get("/matriz/op.acc.6").text
    offer = html.split('id="h-kit"', 1)[1].split("</section>", 1)[0]
    text = tail(offer)

    assert 'href="/herramientas/contrasenas"' in html and ">Añadir las contraseñas<" in html
    for clause in ("op.acc.6.4", "op.acc.6.r1.2", "op.acc.6.r2.1"):
        assert f"{clause}</span>: no cubierta" in offer
    assert "cubierta en parte" not in offer and ": cubierta." not in offer
    assert "no la suspende" in text
    assert not FORBIDDEN.search(offer)


def test_la_ficha_de_op_acc_6_enseña_el_texto_de_las_clausulas_que_la_ficha_carga(web):
    html = web.get("/matriz/op.acc.6").text
    offer = html.split('id="h-kit"', 1)[1].split("</section>", 1)[0]

    # r2.1 es de un refuerzo que la ficha no carga a este nivel: se ve solo el id.
    assert "op.acc.6.r2.1</span>: no cubierta" in offer
    assert "op.acc.6.4</span>: no cubierta. Las credenciales se cambiarán" in tail(offer)


def test_op_acc_6_no_propone_el_kit_con_evidencia(web, signed):
    record(signed, SECOND_OK)

    assert "el kit de contrasenas" not in web.get("/matriz/op.acc.6").text


def test_las_paginas_de_contrasenas_no_prometen_cumplimiento(session, example_store):
    record(example_store, SECOND_FAIL)

    for path in ("/herramientas", "/herramientas/contrasenas", "/matriz/op.acc.6"):
        assert not FORBIDDEN.search(session.get(path).text), path
