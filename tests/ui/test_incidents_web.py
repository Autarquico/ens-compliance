"""Incidentes por la web: alta, reportes intermedios, cierre e historial con las APIs de la CLI."""

import logging
import re

import pytest
from incidents.world import BY, LEVELS, World
from starlette.testclient import TestClient

from ens_evidence.evidence.layout import manifest_key
from ens_evidence.evidence.manifests import list_manifests
from ens_evidence.incidents.verify import scan_incident
from ens_evidence.ui.app import create_ui_app
from ens_evidence.ui.views.incidents import INCIDENT_ROUTES

from .conftest import NOW, ORIGIN, Session, jwks_for, snapshot_of

OPEN = {"title": "Phishing a cuentas", "detected_at": "2026-11-08T06:00", "by": BY}
REPORT = {"by": BY, "action": "Bloqueo de las cuentas afectadas", "action_kind": "emergency"}
CLOSING = {
    "by": BY,
    "conclusion": "Credenciales robadas por phishing; MFA reforzado.",
    "auditable_events_result": "changed",
    "auditable_events_detail": "Se registran ahora los inicios de sesión fallidos.",
    "legal_relevance": "no",
}
INCIDENT = "INC-2026-001"
CLI_TRACE = re.compile(r"--[a-z]|ens-evidence|incident (?:new|update|close|history|verify)|`")


@pytest.fixture
def world(tmp_path) -> World:
    return World(tmp_path)


@pytest.fixture
def ui(config, world, key, token) -> Session:
    app = create_ui_app(
        config, world.store, lambda: NOW, lambda: jwks_for(key), routes=INCIDENT_ROUTES
    )
    return Session(TestClient(app, base_url=ORIGIN), token)


@pytest.fixture
def audit(caplog):
    caplog.set_level(logging.INFO, logger="ens_evidence.ui.audit")
    return caplog


def text_of(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def errors_of(response) -> str:
    return " ".join(re.findall(r"<li>(.*?)</li>", response.text))


def opened(ui: Session, **fields) -> str:
    response = ui.post("/incidentes/nuevo", OPEN | fields)
    assert response.status_code == 303, errors_of(response)
    return response.headers["location"].split("?")[0]


def closed(ui: Session, path: str) -> None:
    assert ui.post(f"{path}/actualizar", {"by": BY, "personal_data": "no"}).status_code == 303
    response = ui.post(f"{path}/cerrar", CLOSING)
    assert response.status_code == 303, errors_of(response)


def history_ids(world: World) -> list[str]:
    return [m.id for m in list_manifests(world.store).manifests if m.source == "incident-history"]


def test_sin_incidentes_la_lista_lo_dice_y_ofrece_registrar(ui):
    response = ui.get("/incidentes")

    assert response.status_code == 200
    assert "Todavía no hay incidentes registrados" in response.text
    assert 'href="/incidentes/nuevo"' in response.text


def test_el_alta_guarda_el_reporte_inicial_como_la_cli(ui, world, audit):
    location = opened(ui, personal_data="unknown")

    assert location == f"/incidentes/{INCIDENT}"
    scan = scan_incident(world.store, INCIDENT)
    (stored,) = scan.records
    assert stored.record.kind.value == "opened" and stored.record.state.title == OPEN["title"]
    assert stored.record.recorded_at == NOW
    assert stored.sha256 in scan.evidence
    assert "step=incidentes action=nuevo" in audit.text
    page = ui.get(f"{location}?hecho=nuevo")
    assert "Incidente registrado" in page.text and OPEN["title"] in page.text
    assert INCIDENT in ui.get("/incidentes").text


def test_el_nombre_de_quien_registra_no_puede_ser_un_correo_y_no_se_guarda_nada(ui, world):
    before = snapshot_of(world.store)

    response = ui.post("/incidentes/nuevo", OPEN | {"by": "ana@example.org"})

    assert response.status_code == 422
    assert "ana@example.org" not in errors_of(response)
    assert "Quién lo registra" in errors_of(response)
    assert not CLI_TRACE.search(errors_of(response))
    assert snapshot_of(world.store) == before


@pytest.mark.parametrize(
    "field, value",
    [
        ("title", "Fallo de Ana, ana@example.org"),
        ("summary", "clave AKIAIOSFODNN7EXAMPLE en el repo"),
        ("title", "DNI 12345678Z del empleado"),
    ],
)
def test_datos_personales_y_secretos_se_rechazan_sin_guardar_ni_repetir_el_valor(
    ui, world, field, value
):
    before = snapshot_of(world.store)

    response = ui.post("/incidentes/nuevo", OPEN | {field: value})

    assert response.status_code == 422
    assert value not in errors_of(response)
    assert errors_of(response) and not CLI_TRACE.search(errors_of(response))
    assert snapshot_of(world.store) == before


@pytest.mark.parametrize(
    "change, expected",
    [
        ({"title": ""}, "Título"),
        ({"detected_at": ""}, "Cuándo se detectó"),
        ({"detected_at": "ayer"}, "Cuándo se detectó"),
        ({"alert_ids": "1700000000.1234"}, "sistema de origen"),
        ({"assets": ["no-existe"]}, "inventario"),
        ({"severity": "catastrófico"}, "nivel"),
    ],
)
def test_un_alta_incompleta_o_con_datos_que_no_existen_se_rechaza_en_llano(
    ui, world, change, expected
):
    before = snapshot_of(world.store)

    response = ui.post("/incidentes/nuevo", OPEN | change)

    assert response.status_code == 422
    assert expected.lower() in errors_of(response).lower()
    assert not CLI_TRACE.search(errors_of(response))
    assert snapshot_of(world.store) == before


def test_la_severidad_se_elige_entre_los_niveles_declarados(ui, world):
    world.declare_levels(LEVELS)

    page = ui.get("/incidentes/nuevo")
    assert '<option value="alto">' in page.text

    path = opened(ui, severity="alto")
    assert "Alto" not in ui.get(path).text  # se guarda con el nombre declarado
    assert scan_incident(world.store, INCIDENT).records[-1].record.state.severity == "alto"


def test_un_reporte_intermedio_anade_la_actuacion_y_conserva_lo_anterior(ui, world, audit):
    path = opened(ui, summary="Inicio del análisis")

    response = ui.post(f"{path}/actualizar", REPORT)

    assert response.status_code == 303 and "Reporte intermedio registrado" in text_of(
        ui.get(response.headers["location"]).text
    )
    scan = scan_incident(world.store, INCIDENT)
    state = scan.records[-1].record.state
    assert [r.record.seq for r in scan.records] == [1, 2]
    assert state.actions[0].description == REPORT["action"] and state.actions[0].at == NOW
    assert state.analysis.summary == "Inicio del análisis"
    assert "step=incidentes action=actualizar" in audit.text


def test_un_reporte_que_no_cambia_nada_se_rechaza(ui, world):
    path = opened(ui)
    before = snapshot_of(world.store)

    response = ui.post(f"{path}/actualizar", {"by": BY})

    assert response.status_code == 422 and "No has cambiado nada" in response.text
    assert snapshot_of(world.store) == before


def test_la_hora_de_la_actuacion_sin_actuacion_se_rechaza(ui):
    path = opened(ui)

    response = ui.post(f"{path}/actualizar", {"by": BY, "action_at": "2026-11-08T07:00"})

    assert response.status_code == 422 and "descríbela" in response.text


def test_no_se_puede_cerrar_sin_decidir_si_afecta_a_datos_personales(ui, world):
    path = opened(ui, personal_data="unknown")
    before = snapshot_of(world.store)

    page = ui.get(path)
    assert "decidir si el incidente afecta a datos personales" in page.text
    assert f"{path}/cerrar" not in page.text
    response = ui.post(f"{path}/cerrar", CLOSING)

    assert response.status_code == 422
    assert "datos personales" in errors_of(response)
    assert snapshot_of(world.store) == before


def test_cerrar_registra_el_reporte_final_el_historial_y_dice_como_enlazarlo(ui, world, audit):
    path = opened(ui)
    closed(ui, path)

    (history,) = history_ids(world)
    state = scan_incident(world.store, INCIDENT).records[-1].record.state
    assert state.status.value == "closed" and state.closure.closed_at == NOW
    page = ui.get(f"{path}?hecho=cerrar")
    assert "Incidente cerrado" in page.text and history in page.text
    assert 'href="/matriz/op.exp.9"' in page.text and 'href="/matriz/op.exp.7"' in page.text
    assert f"{path}/actualizar" not in page.text and f"{path}/cerrar" not in page.text
    assert "step=incidentes action=cerrar" in audit.text


def test_un_incidente_cerrado_no_admite_mas_registros(ui, world):
    path = opened(ui)
    closed(ui, path)
    before = snapshot_of(world.store)

    for action, data in (("actualizar", REPORT), ("cerrar", CLOSING)):
        response = ui.post(f"{path}/{action}", data)
        assert response.status_code == 422
        assert "cerrado" in errors_of(response) and not CLI_TRACE.search(errors_of(response))

    assert snapshot_of(world.store) == before


def test_con_relevancia_jurisdiccional_hay_que_citar_asesor_y_evidencias(ui, world):
    path = opened(ui)
    ui.post(f"{path}/actualizar", {"by": BY, "personal_data": "no"})
    before = snapshot_of(world.store)

    response = ui.post(f"{path}/cerrar", CLOSING | {"legal_relevance": "yes"})

    assert response.status_code == 422
    assert "relevancia jurisdiccional" in errors_of(response)
    assert snapshot_of(world.store) == before


def test_un_cierre_incompleto_senala_lo_que_falta(ui):
    path = opened(ui)
    ui.post(f"{path}/actualizar", {"by": BY, "personal_data": "no"})

    response = ui.post(
        f"{path}/cerrar", CLOSING | {"conclusion": "", "auditable_events_detail": ""}
    )

    assert response.status_code == 422
    assert "Conclusión" in errors_of(response) and "Eventos auditables" in errors_of(response)


def test_un_simulacro_exige_el_procedimiento_aprobado(ui, world):
    before = snapshot_of(world.store)

    response = ui.post("/incidentes/nuevo", OPEN | {"simulated": "1"})

    assert response.status_code == 422
    assert "procedimiento" in errors_of(response).lower()
    assert snapshot_of(world.store) == before


def test_un_simulacro_lleva_el_prefijo_sim_y_se_muestra_como_tal(ui, world):
    world.approve_procedure()

    path = opened(ui, simulated="1", title="Simulacro: ransomware en un portátil")

    assert path == "/incidentes/SIM-2026-001"
    assert "Simulacro" in ui.get(path).text and "no un incidente real" in ui.get(path).text
    listing = ui.get("/incidentes").text
    assert "SIM-2026-001" in listing and "Simulacro" in listing
    closed(ui, path)
    assert len(history_ids(world)) == 1


def test_si_falta_el_historial_se_completa_sin_tocar_los_registros(ui, world, audit):
    path = opened(ui)
    closed(ui, path)
    (history,) = history_ids(world)
    world.remove(manifest_key(history))
    records = {k: v for k, v in snapshot_of(world.store).items() if k.startswith("incidents/")}

    page = ui.get(path)
    assert "Falta el historial" in page.text and f"{path}/historial" in page.text
    response = ui.post(f"{path}/historial", {"by": BY})

    assert response.status_code == 303
    assert len(history_ids(world)) == 1
    assert {k: v for k, v in snapshot_of(world.store).items() if k in records} == records
    assert "Falta el historial" not in ui.get(path).text
    assert "step=incidentes action=historial" in audit.text


def test_ids_y_acciones_desconocidos_son_404(ui):
    path = opened(ui)

    assert ui.get("/incidentes/INC-2026-099").status_code == 404
    assert ui.get("/incidentes/no-es-un-id").status_code == 404
    assert ui.post(f"{path}/borrar", {"by": BY}).status_code == 404
    assert ui.post("/incidentes/INC-2026-099/actualizar", REPORT).status_code == 404


def test_las_paginas_no_llevan_ordenes_de_la_cli_ni_promesas_de_conformidad(ui, world):
    path = opened(ui)
    ui.post(f"{path}/actualizar", {"by": BY, "personal_data": "no"})
    pages = [ui.get(p).text for p in ("/incidentes", "/incidentes/nuevo", path)]
    ui.post(f"{path}/cerrar", CLOSING)
    pages.append(ui.get(path).text)

    for html in pages:
        assert not CLI_TRACE.search(text_of(html)), html[:80]
        assert not re.search(r"certifica|garantiza el cumplimiento|cumple el ens", html, re.I)
        assert "Revisión humana obligatoria" in html
