"""Copy: nunca certifica ni garantiza el cumplimiento; siempre recuerda la revisión humana."""

import re

import pytest

from ens_evidence.catalog import load_catalog
from ens_evidence.ui.wizard import STEPS

from . import test_equivalence as flows
from .conftest import Session, auth, ui_client
from .test_hostile import VIEWS

web_flow = flows.web_flow  # el flujo terminado, una vez por sesión de pruebas

FORBIDDEN = re.compile(r"certific|garantiza el cumplimiento|cumple el ENS", re.IGNORECASE)
STEP_PATHS = [f"/asistente/{s.slug}" for s in STEPS]
PAGES = [*VIEWS, "/evidencias/nueva", *STEP_PATHS]


def rendered(session: Session, path: str):
    return session.get(path)


@pytest.mark.parametrize("path", PAGES)
def test_ninguna_vista_dice_certifica_ni_garantiza_ni_cumple(session, path):
    response = rendered(session, path)

    assert response.status_code == 200
    assert not FORBIDDEN.search(response.text), FORBIDDEN.search(response.text)


@pytest.mark.parametrize("path", PAGES)
def test_ninguna_vista_dice_certifica_con_el_asistente_completado(completed, path):
    response = rendered(completed, path)

    assert response.status_code == 200
    assert not FORBIDDEN.search(response.text), FORBIDDEN.search(response.text)


def test_ninguna_pagina_de_error_ni_paso_con_errores_dice_certifica(session, token):
    pages = [
        session.get("/asistente/nope"),
        session.post("/asistente/nope", {}),
        session.post("/asistente/sistema", {"name": ""}),
        session.post("/asistente/sistema", {"name": ""}, htmx=True),
        session.post("/asistente/aprobacion-alcance", {"accion": "aprobar", "approved_by": ""}),
    ]
    for response in pages:
        assert response.status_code in (404, 422, 200), response.status_code
        assert not FORBIDDEN.search(response.text)


@pytest.mark.parametrize("path", ["/preauditoria", "/expediente"])
def test_preauditoria_y_expediente_recuerdan_la_revision_humana(session, completed, path):
    assert "revisión humana" in rendered(session, path).text.lower()


@pytest.mark.parametrize("path", ["/preauditoria", "/expediente"])
def test_preauditoria_y_expediente_recuerdan_la_revision_humana_sin_datos(raw_client, token, path):
    response = raw_client.get(path, headers=auth(token))
    assert "revisión humana" in response.text.lower()


def without_official_names(html: str) -> str:
    """Los nombres oficiales del catálogo («Certificados», …) no son una afirmación."""
    for card in load_catalog().measures:
        for name in (card.name, *(r.title for r in card.reinforcements)):
            if FORBIDDEN.search(name):
                html = html.replace(name, "")
    return html


def test_ninguna_pagina_del_flujo_terminado_dice_certifica(web_flow, config, key, token):
    session = Session(ui_client(config, web_flow.store, key), token)
    pages = [
        *PAGES,
        "/matriz/org.1",
        "/matriz/op.exp.9",
        f"/expediente/{web_flow.dossier}",
    ]

    for path in pages:
        response = session.get(path)
        assert response.status_code == 200, path
        found = FORBIDDEN.search(without_official_names(response.text))
        assert not found, (path, found)
