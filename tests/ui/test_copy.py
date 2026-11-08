"""Copy: nunca certifica ni garantiza el cumplimiento; siempre recuerda la revisión humana."""

import re

import pytest

from ens_evidence.ui.wizard import STEPS

from .conftest import Session, auth
from .test_hostile import VIEWS

FORBIDDEN = re.compile(r"certific|garantiza el cumplimiento|cumple el ENS", re.IGNORECASE)
STEP_PATHS = [f"/asistente/{s.slug}" for s in STEPS]
PAGES = [*VIEWS, *STEP_PATHS]


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
