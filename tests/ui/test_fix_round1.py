"""Regresiones de la ronda 1 de revisión: errores en HTML, auditoría y copy del flujo completo."""

import logging

from .conftest import WRITE_HEADERS, Session, auth, ui_client
from .test_equivalence import ui_script


def completed(config, store, key, token) -> Session:
    session = Session(ui_client(config, store, key), token)
    ui_script(session)
    return session


# --- CODE-2: 404 y 405 en HTML -----------------------------------------------------------------


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


# --- SEC-1: auditoría de escrituras ------------------------------------------------------------


def test_cada_escritura_deja_una_linea_de_auditoria_sin_cuerpo(session, caplog):
    caplog.set_level(logging.INFO, logger="ens_evidence.ui.audit")

    response = session.post(
        "/asistente/aprobacion-alcance", {"accion": "aprobar", "approved_by": "Marta Gil"}
    )

    assert response.status_code == 303
    lines = [r.getMessage() for r in caplog.records if r.name == "ens_evidence.ui.audit"]
    assert len(lines) == 1
    line = lines[0]
    assert "identity=ana@example.com" in line
    assert "step=aprobacion-alcance" in line and "action=aprobar" in line
    assert "scope/approval.yaml" in line and "2026-11-08T12:00:00" in line
    assert "Marta Gil" not in line


def test_lo_que_no_se_guarda_no_deja_auditoria(session, caplog):
    caplog.set_level(logging.INFO, logger="ens_evidence.ui.audit")

    session.post("/asistente/aprobacion-alcance", {"accion": "aprobar", "approved_by": ""})
    session.post("/asistente/dependencias", {"purpose": ""})

    assert not [r for r in caplog.records if r.name == "ens_evidence.ui.audit"]


# --- PROD-1..4 ---------------------------------------------------------------------------------


def test_estado_con_el_asistente_completo_habla_en_claro(config, example_store, key, token):
    session = completed(config, example_store, key, token)

    page = session.get("/").text

    assert "applicability status" not in page
    assert "UNDETERMINED_APPLICABILITY" not in page
    assert "Aplicabilidad sin decidir" in page
    assert "Declaración de Aplicabilidad" in page


def test_matriz_vacia_explica_la_declaracion_cuando_el_asistente_esta_hecho(
    config, example_store, key, token
):
    session = completed(config, example_store, key, token)

    assert "se llena cuando se firma la Declaración de Aplicabilidad" in session.get("/matriz").text


def test_matriz_vacia_pide_el_asistente_si_falta(session):
    assert "Termina antes el alcance" in session.get("/matriz").text


def test_dependencias_es_opcional_con_el_alcance_aprobado(config, example_store, key, token):
    session = completed(config, example_store, key, token)

    assert session.get("/asistente").headers["location"] == "/"
    page = session.get("/asistente/sistema").text
    assert "Dependencias (opcional)" in page


def test_guardar_sin_cambios_no_dice_guardado(session):
    response = session.post("/asistente/aprobacion-alcance", {"accion": "guardar"})

    assert response.status_code == 303
    assert "guardado" not in response.headers["location"]
    assert "Guardar" not in session.get("/asistente/aprobacion-alcance").text


# --- PROD-6..8 ---------------------------------------------------------------------------------


def test_activo_con_espacios_y_booleano_invalido_dan_error_de_campo(session):
    response = session.post(
        "/asistente/activos",
        {
            "accion": "anadir", "asset_type": "laptop", "asset_id": "mi portatil",
            "asset_os": "macOS", "asset_owner": "Ana", "asset_encrypted": "quizá",
            "asset_usage": "x",
        },
    )  # fmt: skip

    assert response.status_code == 422
    assert 'id="err-asset_id"' in response.text
    assert 'id="err-asset_encrypted"' in response.text
    assert "encrypted:" not in response.text


def test_lo_ya_incluido_no_se_puede_desmarcar(session):
    page = session.get("/asistente/activos").text

    assert "checked disabled" in page
    assert 'type="hidden" name="included_assets"' in page
