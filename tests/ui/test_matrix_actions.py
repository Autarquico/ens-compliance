"""La matriz se trabaja desde la web: responsable, enlaces y revisión con las APIs de la CLI."""

import logging
import re

import pytest
from applicability.helpers import (
    LATER,
    decide,
    decide_all_undetermined,
    prepared_workspace,
    uniform,
)
from matrix.acceptance_helpers import generate_and_approve_documents
from matrix.helpers import COMPENSATION, SIGNED_AT
from starlette.testclient import TestClient

from ens_evidence.applicability import approve_declaration, propose_applicability
from ens_evidence.matrix import read_matrix, read_reviews
from ens_evidence.models.enums import Applicability
from ens_evidence.ui.app import create_ui_app
from ens_evidence.ui.views.matrix import MATRIX_ROUTES, plain

from .conftest import NOW, ORIGIN, Session, jwks_for, snapshot_of

MEASURE = "org.1"
ALL_CLAUSES = [f"org.1.{n}" for n in range(1, 6)]
NOT_APPLICABLE = "op.cont.3"
OWNER = {"owner_name": "Ana Ruiz", "owner_role": "system_administrator", "every": "90"}
REVIEW = {
    "by": "Ana Ruiz",
    "role": "system_administrator",
    "assessment": "COMPLIANT",
    "conclusion": "Revisada con la política aprobada.",
}


@pytest.fixture
def signed(tmp_path):
    """Alcance, categorización y Declaración vigentes, y los documentos de la organización."""
    store = prepared_workspace(tmp_path / "signed", uniform("MEDIO"))
    propose_applicability(store, LATER)
    decide_all_undetermined(store)
    decide(store, "op.pl.1", Applicability.APPLICABLE, "Compensada.", compensation=COMPENSATION)
    decide(store, "op.cont.2", Applicability.APPLICABLE, "Seleccionada como adicional.")
    approve_declaration(store, "Luis Pardo", SIGNED_AT)
    generate_and_approve_documents(store)
    return store


@pytest.fixture
def web(config, signed, key, token) -> Session:
    app = create_ui_app(config, signed, lambda: NOW, lambda: jwks_for(key), routes=[*MATRIX_ROUTES])
    return Session(TestClient(app, base_url=ORIGIN), token)


def matrix_of(store):
    matrix, errors = read_matrix(store)
    assert not errors
    return matrix


def entry_of(store, measure=MEASURE):
    return next(e for e in matrix_of(store).entries if e.id == measure)


def prepared(web, signed):
    """Responsable y periodicidad fijados y la política enlazada a las cinco cláusulas."""
    web.get("/matriz")
    assert web.post(f"/matriz/{MEASURE}/responsable", OWNER).status_code == 303
    link = {"kind": "document", "target": "politica-seguridad", "covers": ALL_CLAUSES}
    assert web.post(f"/matriz/{MEASURE}/enlazar", link).status_code == 303


def test_lista_crea_la_matriz_y_enlaza_cada_medida(web, signed):
    assert not signed.exists("controls/matrix.yaml")

    page = web.get("/matriz")

    assert signed.exists("controls/matrix.yaml")
    assert f'href="/matriz/{MEASURE}"' in page.text


def test_la_lista_no_reescribe_una_matriz_existente(web, signed):
    web.get("/matriz")
    before = snapshot_of(signed)

    web.get("/matriz")

    assert snapshot_of(signed) == before


def test_medida_muestra_clausulas_y_propone_las_del_documento(web):
    page = web.get(f"/matriz/{MEASURE}").text

    assert "Qué pide la medida" in page
    for clause in ALL_CLAUSES:
        assert f'name="covers" value="{clause}" checked' in page
    assert "org.1.intro" not in page
    assert "politica-seguridad" in page


def test_la_propuesta_solo_marca_lo_que_dice_la_ficha(web):
    page = web.get(f"/matriz/{MEASURE}").text

    form = page.split("designacion-responsables · ")[1].split("</form>")[0]
    assert 'value="org.1.3" checked' in form
    assert 'value="org.1.1" checked' not in form


def test_medida_desconocida_es_404(web):
    assert web.get("/matriz/no.existe").status_code == 404
    assert web.post("/matriz/no.existe/responsable", OWNER).status_code == 404


def test_accion_desconocida_es_404(web):
    assert web.post(f"/matriz/{MEASURE}/borrar", {"x": "1"}).status_code == 404


def test_fija_responsable_y_periodicidad_y_lo_audita(web, signed, caplog):
    web.get("/matriz")
    caplog.set_level(logging.INFO, logger="ens_evidence.ui.audit")

    response = web.post(f"/matriz/{MEASURE}/responsable", OWNER)

    assert response.status_code == 303
    assert response.headers["location"] == f"/matriz/{MEASURE}?guardado=1"
    entry = entry_of(signed)
    assert (entry.owner.name, entry.owner.role.value, entry.review_every_days) == (
        "Ana Ruiz",
        "system_administrator",
        90,
    )
    assert "identity=ana@example.com step=matriz action=responsable" in caplog.text
    assert "Ana Ruiz" not in caplog.text


@pytest.mark.parametrize(
    "form",
    [
        {"owner_name": ""},
        {"owner_name": "Ana", "owner_role": "jefe"},
        {"every": "0"},
        {"every": "x"},
    ],
)
def test_responsable_invalido_no_escribe(web, signed, form):
    web.get("/matriz")
    before = snapshot_of(signed)

    response = web.post(f"/matriz/{MEASURE}/responsable", form)

    assert response.status_code == 422
    assert "No se ha guardado" in response.text
    assert snapshot_of(signed) == before


def test_enlaza_un_documento_con_las_clausulas_marcadas(web, signed):
    web.get("/matriz")
    link = {"kind": "document", "target": "politica-seguridad", "covers": ["org.1.1", "org.1.2"]}

    assert web.post(f"/matriz/{MEASURE}/enlazar", link).status_code == 303

    saved = entry_of(signed).links[0]
    assert (saved.target, saved.covers) == (
        ("org_document", "politica-seguridad"),
        ["org.1.1", "org.1.2"],
    )
    assert "Quitar enlace" in web.get(f"/matriz/{MEASURE}").text


def test_enlace_sin_clausulas_o_a_algo_desconocido_se_rechaza_en_claro(web, signed):
    web.get("/matriz")
    before = snapshot_of(signed)

    sin_clausulas = web.post(
        f"/matriz/{MEASURE}/enlazar", {"kind": "document", "target": "politica-seguridad"}
    )
    desconocido = web.post(
        f"/matriz/{MEASURE}/enlazar",
        {"kind": "document", "target": "no-existe", "covers": ["org.1.1"]},
    )

    assert sin_clausulas.status_code == desconocido.status_code == 422
    assert "Marca al menos una cláusula" in sin_clausulas.text
    assert "Documento desconocido" in desconocido.text
    assert snapshot_of(signed) == before


def test_quita_un_enlace(web, signed):
    prepared(web, signed)

    response = web.post(
        f"/matriz/{MEASURE}/desenlazar", {"kind": "org_document", "target": "politica-seguridad"}
    )

    assert response.status_code == 303
    assert entry_of(signed).links == []


def test_quitar_un_enlace_inexistente_se_rechaza(web, signed):
    web.get("/matriz")

    response = web.post(
        f"/matriz/{MEASURE}/desenlazar", {"kind": "org_document", "target": "politica-seguridad"}
    )

    assert response.status_code == 422


def test_revision_favorable_con_clausulas_sin_cubrir_dice_cuales_y_no_escribe(web, signed):
    web.get("/matriz")
    web.post(f"/matriz/{MEASURE}/responsable", OWNER)
    web.post(
        f"/matriz/{MEASURE}/enlazar",
        {"kind": "document", "target": "politica-seguridad", "covers": ["org.1.1", "org.1.2"]},
    )
    before = snapshot_of(signed)

    response = web.post(f"/matriz/{MEASURE}/revisar", REVIEW)

    assert response.status_code == 422
    for clause in ("org.1.3", "org.1.4", "org.1.5"):
        assert clause in response.text
    assert "Cláusulas sin cubrir" in response.text
    assert snapshot_of(signed) == before


def test_revision_favorable_con_todo_cubierto_se_registra(web, signed, caplog):
    prepared(web, signed)
    caplog.set_level(logging.INFO, logger="ens_evidence.ui.audit")

    response = web.post(f"/matriz/{MEASURE}/revisar", REVIEW)

    assert response.status_code == 303
    found, errors = read_reviews(signed, MEASURE)
    assert not errors
    assert [r.record.assessment.value for r in found] == ["COMPLIANT"]
    assert "action=revisar" in caplog.text
    page = web.get(f"/matriz/{MEASURE}").text
    assert "Ana Ruiz" in page and "Revisada con la política aprobada." in page
    assert "Favorable" in page


def test_revision_sin_responsable_se_rechaza_sin_ordenes_de_la_cli(web, signed):
    web.get("/matriz")
    before = snapshot_of(signed)

    response = web.post(f"/matriz/{MEASURE}/revisar", REVIEW)

    assert response.status_code == 422
    assert "Falta el responsable" in response.text
    assert "Falta la periodicidad" in response.text
    assert "`" not in response.text and "--" not in response.text
    assert "matrix" not in response.text.lower().replace("matriz", "")
    assert snapshot_of(signed) == before


@pytest.mark.parametrize(
    "change",
    [{"by": ""}, {"conclusion": ""}, {"role": "management"}, {"assessment": ""}],
)
def test_revision_incompleta_no_escribe(web, signed, change):
    prepared(web, signed)
    before = snapshot_of(signed)

    response = web.post(f"/matriz/{MEASURE}/revisar", {**REVIEW, **change})

    assert response.status_code == 422
    assert snapshot_of(signed) == before


def test_medida_que_no_aplica_se_revisa_sin_valoracion(web, signed):
    web.get("/matriz")
    page = web.get(f"/matriz/{NOT_APPLICABLE}").text
    assert 'name="assessment"' not in page
    web.post(f"/matriz/{NOT_APPLICABLE}/responsable", OWNER)

    response = web.post(
        f"/matriz/{NOT_APPLICABLE}/revisar",
        {"by": "Ana Ruiz", "role": "system_administrator", "conclusion": "No aplica."},
    )

    assert response.status_code == 303


def test_el_by_no_se_prellena_con_el_email(web):
    page = web.get(f"/matriz/{MEASURE}").text

    assert 'name="by" value=""' in page
    assert "Queda registrado como lo declaras." in page


def test_un_email_como_revisor_se_rechaza_y_no_escribe(web, signed):
    prepared(web, signed)
    before = snapshot_of(signed)

    response = web.post(f"/matriz/{MEASURE}/revisar", {**REVIEW, "by": "ana@example.org"})

    assert response.status_code == 422
    assert "indica solo un nombre" in response.text
    assert snapshot_of(signed) == before


def test_origen_ajeno_no_escribe(web, signed):
    web.get("/matriz")
    before = snapshot_of(signed)

    response = web._client.post(
        f"/matriz/{MEASURE}/responsable",
        data=OWNER,
        headers={"Cf-Access-Jwt-Assertion": web._token, "Origin": "https://evil.example"},
    )

    assert response.status_code == 403
    assert snapshot_of(signed) == before


def test_lo_escrito_es_lo_que_escribe_la_api(web, signed, tmp_path):
    from ens_evidence.matrix import add_link, build_link, init_matrix, set_entry

    twin = prepared_workspace(tmp_path / "twin", uniform("MEDIO"))
    propose_applicability(twin, LATER)
    decide_all_undetermined(twin)
    decide(twin, "op.pl.1", Applicability.APPLICABLE, "Compensada.", compensation=COMPENSATION)
    decide(twin, "op.cont.2", Applicability.APPLICABLE, "Seleccionada como adicional.")
    approve_declaration(twin, "Luis Pardo", SIGNED_AT)
    generate_and_approve_documents(twin)
    init_matrix(twin, NOW)
    set_entry(twin, NOW, MEASURE, owner_name="Ana Ruiz", owner_role="system_administrator")
    set_entry(twin, NOW, MEASURE, every_days=90)
    add_link(twin, NOW, MEASURE, build_link("org_document", "politica-seguridad", ALL_CLAUSES))

    prepared(web, signed)

    assert entry_of(signed) == entry_of(twin)


@pytest.mark.parametrize(
    "raw,expected_absent",
    [
        ("falta el responsable (matrix set --owner NOMBRE --owner-role ROL).", "--owner"),
        ("La Declaración no está vigente: sin firmar; ver `applicability status`.", "`"),
        ("Siguiente paso: importa una nueva (evidence import --source manual)", "--source"),
    ],
)
def test_plain_quita_las_ordenes(raw, expected_absent):
    assert expected_absent not in plain(raw)
    assert re.search(r"\.$", plain(raw))


def test_la_pagina_no_afirma_conformidad(web):
    page = web.get(f"/matriz/{MEASURE}").text.lower()

    for phrase in ("certifica", "garantiza el cumplimiento", "cumple el ens"):
        assert phrase not in page
