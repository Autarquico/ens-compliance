"""Expediente por la web: generar, autoevaluar, analizar, declarar y descargar."""

import logging
import re
import shutil
from datetime import timedelta

import pytest
from assessment.dossier.transition_helpers import docs_world
from starlette.testclient import TestClient

from ens_evidence.assessment.dossier import build_snapshot, current_inputs, render_snapshot_md
from ens_evidence.assessment.dossier.store import save_markdown, save_snapshot
from ens_evidence.models.enums import DossierState, Role
from ens_evidence.ui.app import create_ui_app
from ens_evidence.ui.views import dossier as dossier_view
from ens_evidence.ui.views.dossier import DOSSIER_ROUTES
from ens_evidence.ui.views.status import STATUS_ROUTES
from ens_evidence.workspace import LocalStore

from .conftest import NOW, ORIGIN, Session, jwks_for, snapshot_of


@pytest.fixture(scope="module")
def world_template(tmp_path_factory):
    path = tmp_path_factory.mktemp("docs") / "ws"
    docs_world(path)
    return path


@pytest.fixture
def world(world_template, tmp_path) -> LocalStore:
    shutil.copytree(world_template, tmp_path / "ws")
    return LocalStore(tmp_path / "ws")


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now

    def tick(self):
        self.now += timedelta(minutes=2)


@pytest.fixture
def clock() -> Clock:
    return Clock()


def web(config, store, key, token, clock) -> Session:
    app = create_ui_app(
        config, store, clock, lambda: jwks_for(key), routes=[*STATUS_ROUTES, *DOSSIER_ROUTES]
    )
    return Session(TestClient(app, base_url=ORIGIN), token)


@pytest.fixture
def ui(config, world, key, token, clock) -> Session:
    return web(config, world, key, token, clock)


def generated(ui: Session) -> str:
    response = ui.post("/expediente/generar", {})
    assert response.status_code == 303
    return response.headers["location"].split("?")[0].rsplit("/", 1)[1]


def text_of(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


@pytest.fixture
def audit(caplog):
    caplog.set_level(logging.INFO, logger="ens_evidence.ui.audit")
    return caplog


# --- generar -------------------------------------------------------------------------------------


def test_generar_escribe_lo_mismo_que_la_cli(ui, world, world_template, tmp_path, clock):
    other = tmp_path / "cli"
    shutil.copytree(world_template, other)
    cli_store = LocalStore(other)
    snapshot = build_snapshot(cli_store, current_inputs(clock.now))
    cli_id = save_snapshot(cli_store, snapshot)
    save_markdown(cli_store, cli_id, render_snapshot_md(snapshot))

    dossier = generated(ui)

    assert dossier == cli_id
    assert snapshot_of(world) == snapshot_of(cli_store)


def test_generar_deja_su_linea_de_auditoria(ui, audit):
    dossier = generated(ui)

    line = next(r.message for r in audit.records if "step=expediente" in r.message)
    assert "identity=ana@example.com" in line and "action=generar" in line
    assert f"evidence/assessments/{dossier}/dossier.json" in line


def test_generar_sin_contexto_vigente_lo_dice_y_no_escribe(config, store, key, token, clock):
    session = web(config, store, key, token, clock)

    response = session.post("/expediente/generar", {})

    assert response.status_code == 422
    assert "Antes de generar el expediente hay que" in response.text
    assert snapshot_of(store) == {}


def test_la_pagina_ofrece_el_siguiente_paso_y_la_descarga(ui):
    dossier = generated(ui)

    page = ui.get(f"/expediente/{dossier}").text

    assert "Expediente generado." not in page
    assert f'action="/expediente/{dossier}/self-assess"' in page
    assert f'action="/expediente/{dossier}/declare"' not in page
    assert 'name="by" value=""' in page
    assert "ana@example.com" not in page.split('name="by"')[1].split(">")[0]
    assert f'href="/expediente/{dossier}/dossier.md"' in page


def test_la_pagina_del_ultimo_expediente_es_la_de_su_id(ui):
    dossier = generated(ui)

    assert dossier in ui.get("/expediente").text


def test_descargar_sirve_el_markdown_tal_cual(ui, world):
    dossier = generated(ui)

    response = ui.get(f"/expediente/{dossier}/dossier.md")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert response.headers["content-disposition"] == (
        f'attachment; filename="expediente-{dossier}.md"'
    )
    assert response.content == world.read_bytes(f"evidence/assessments/{dossier}/dossier.md")


@pytest.mark.parametrize(
    "path",
    [
        "/expediente/no-es-un-id",
        "/expediente/..%2F..%2Fscope/dossier.md",
        "/expediente/20261108T120000000000Z-000000000000",
        "/expediente/20261108T120000000000Z-000000000000/dossier.md",
    ],
)
def test_un_id_que_no_existe_es_404(ui, path):
    assert ui.get(path).status_code == 404


# --- transiciones --------------------------------------------------------------------------------


def test_la_autoevaluacion_sin_revisiones_se_rechaza_en_llano_y_no_escribe(ui, world, clock):
    dossier = generated(ui)
    before = snapshot_of(world)
    clock.tick()

    response = ui.post(
        f"/expediente/{dossier}/self-assess", {"by": "Ana", "role": "system_administrator"}
    )

    assert response.status_code == 422
    page = text_of(response.text)
    assert "No se registra la autoevaluación" in page
    assert "SELF_ASSESSED" not in page
    assert snapshot_of(world) == before


def test_analizar_antes_de_autoevaluar_dice_que_falta_la_autoevaluacion(ui, clock):
    dossier = generated(ui)
    clock.tick()

    response = ui.post(
        f"/expediente/{dossier}/analyze",
        {"by": "Lu", "conclusion": "Revisado", "elevated_to": "Hugo"},
    )

    assert response.status_code == 422
    assert "exige antes la autoevaluación" in text_of(response.text)


class Spy:
    def __init__(self):
        self.calls: list[tuple] = []

    def __call__(self, store, dossier, to_state, **kwargs):
        self.calls.append((dossier, to_state, kwargs))


@pytest.fixture
def spy(monkeypatch) -> Spy:
    spy = Spy()
    monkeypatch.setattr(dossier_view, "record_transition", spy)
    return spy


def test_autoevaluar_registra_con_el_rol_elegido(ui, spy, audit, clock):
    dossier = generated(ui)
    clock.tick()

    response = ui.post(
        f"/expediente/{dossier}/self-assess",
        {"by": "Ana Ruiz", "role": "system_administrator_delegate"},
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/expediente/{dossier}?hecho=self-assess"
    [(called, state, kwargs)] = spy.calls
    assert (called, state) == (dossier, DossierState.SELF_ASSESSED)
    assert kwargs["by"] == "Ana Ruiz"
    assert kwargs["role"] is Role.SYSTEM_ADMINISTRATOR_DELEGATE
    assert kwargs["at"] == kwargs["now"] == clock.now


def test_analizar_pasa_conclusion_elevacion_y_confirmaciones(ui, spy):
    dossier = generated(ui)

    response = ui.post(
        f"/expediente/{dossier}/analyze",
        {
            "by": "Lucía",
            "conclusion": "Sin desviaciones relevantes",
            "elevated_to": "Hugo Serrano",
            "elevated": "1",
            "manual_judgment_review": "1",
            "corrective_measures": "Cifrar los portátiles\n\n  Revisar accesos \n",
        },
    )

    assert response.status_code == 303
    [(_, state, kwargs)] = spy.calls
    assert state is DossierState.ANALYZED and kwargs["role"] is Role.SECURITY_OFFICER
    assert kwargs["conclusions"] == "Sin desviaciones relevantes"
    assert kwargs["elevated_to"] == "Hugo Serrano"
    assert kwargs["elevation_confirmed"] and kwargs["manual_judgment_review"]
    assert [m.description for m in kwargs["corrective_measures"]] == [
        "Cifrar los portátiles",
        "Revisar accesos",
    ]
    assert all(m.addressee is Role.SYSTEM_OWNER for m in kwargs["corrective_measures"])


def test_analizar_sin_confirmaciones_las_pasa_como_no_confirmadas(ui, spy):
    dossier = generated(ui)

    ui.post(
        f"/expediente/{dossier}/analyze",
        {"by": "Lucía", "conclusion": "Revisado", "elevated_to": "Hugo"},
    )

    [(_, _, kwargs)] = spy.calls
    assert not kwargs["elevation_confirmed"] and not kwargs["manual_judgment_review"]


def test_declarar_es_de_la_direccion_con_reconocimiento_y_evidencia(ui, spy):
    dossier = generated(ui)

    response = ui.post(
        f"/expediente/{dossier}/declare",
        {"by": "Marta Ibáñez", "evidence": "EV-20261108-abcdef12", "acknowledge_incomplete": "1"},
    )

    assert response.status_code == 303
    [(_, state, kwargs)] = spy.calls
    assert state is DossierState.DECLARED and kwargs["role"] is Role.MANAGEMENT
    assert kwargs["declaration_evidence_id"] == "EV-20261108-abcdef12"
    assert kwargs["acknowledge_incomplete"] is True


def test_declarar_sin_reconocer_ni_evidencia_pasa_vacio(ui, spy):
    dossier = generated(ui)

    ui.post(f"/expediente/{dossier}/declare", {"by": "Marta"})

    [(_, _, kwargs)] = spy.calls
    assert kwargs["declaration_evidence_id"] is None
    assert kwargs["acknowledge_incomplete"] is False


@pytest.mark.parametrize(
    ("action", "data", "missing"),
    [
        ("self-assess", {"by": " ", "role": "system_administrator"}, "by"),
        ("self-assess", {"by": "Ana", "role": "management"}, "role"),
        ("analyze", {"by": "Lu", "conclusion": "", "elevated_to": "Hugo"}, "conclusion"),
        ("analyze", {"by": "Lu", "conclusion": "Ok", "elevated_to": ""}, "elevated_to"),
        ("declare", {"by": ""}, "by"),
    ],
)
def test_faltan_datos_se_marca_el_campo_y_no_se_registra(ui, spy, action, data, missing):
    dossier = generated(ui)

    response = ui.post(f"/expediente/{dossier}/{action}", data)

    assert response.status_code == 422
    assert "es obligatorio" in response.text or "Elige uno" in response.text
    assert spy.calls == []


def test_un_correo_en_quien_se_rechaza_en_castellano_sin_by(ui, spy):
    dossier = generated(ui)

    response = ui.post(
        f"/expediente/{dossier}/self-assess",
        {"by": "ana@example.com", "role": "system_administrator"},
    )

    assert response.status_code == 422
    assert "Quién lo registra" in response.text
    assert "--by" not in response.text
    assert spy.calls == []


def test_lo_enviado_se_devuelve_escapado(ui, spy):
    dossier = generated(ui)

    response = ui.post(
        f"/expediente/{dossier}/analyze",
        {"by": "<script>alert(1)</script>", "conclusion": "", "elevated_to": "Hugo"},
    )

    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;" in response.text


def test_una_accion_desconocida_es_404(ui):
    dossier = generated(ui)

    assert ui.post(f"/expediente/{dossier}/borrar", {"by": "Ana"}).status_code == 404


def test_un_formulario_enorme_se_rechaza(ui):
    dossier = generated(ui)

    response = ui.post(f"/expediente/{dossier}/analyze", {"by": "Ana", "conclusion": "x" * 300_000})

    assert response.status_code == 413


def test_ver_el_expediente_no_escribe(ui, world):
    dossier = generated(ui)
    before = snapshot_of(world)

    for path in ("/expediente", f"/expediente/{dossier}", f"/expediente/{dossier}/dossier.md"):
        assert ui.get(path).status_code == 200

    assert snapshot_of(world) == before
