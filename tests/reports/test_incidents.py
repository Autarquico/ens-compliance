"""Informes diario y mensual frente al registro de incidentes (ADR 0013)."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from incidents.helpers import DETECTED, RECORDED
from incidents.world import World

from ens_evidence.evidence.layout import manifest_key
from ens_evidence.incidents.verify import scan_incident
from ens_evidence.reports.model import Sourced, Unavailable, render_json
from ens_evidence.reports.monthly import build_monthly

from .helpers import source_sha256
from .world import MADRID, daily_at, source_of

AFTER_ALL = datetime(2026, 11, 2, 5, 30, tzinfo=UTC)
MONTH = "2026-10"
GENERATED = datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
FREE_TEXT = "Credenciales reutilizadas por el atacante"


@pytest.fixture
def world(tmp_path) -> World:
    return World(tmp_path)


def monthly_at(world, *, generated_at=GENERATED):
    return build_monthly(
        world.store, generated_at=generated_at, period=MONTH, tz=MADRID, checks=source_of()
    )


def daily_report(world, *, generated_at=AFTER_ALL):
    return daily_at(world.store, source_of(), generated_at=generated_at)


def open_incident(world, incident_id="INC-2026-001", **fields):
    world.incident_id = incident_id
    if "severity" in fields:
        world.declare_levels()
    return world.open(id=incident_id, **fields)


def record_keys(world, incident_id):
    return [s.key for s in scan_incident(world.store, incident_id).records]


def test_sin_incidentes_la_lista_vacia_cita_el_prefijo_consultado(world):
    report = daily_report(world)

    assert isinstance(report.open_incidents, Sourced)
    assert report.open_incidents.value == []
    (source,) = report.open_incidents.sources
    assert (source.kind, source.key) == ("incident", "incidents/")
    assert source.sha256 == source_sha256(world.store, "incidents/")


def test_incidente_abierto_muestra_sus_valores_y_fuentes_reales(world):
    open_incident(world, severity="alto")
    world.update(analysis={"summary": FREE_TEXT})

    section = daily_report(world).open_incidents

    (entry,) = section.value
    assert (entry.id, entry.title, entry.severity) == (
        "INC-2026-001",
        "Acceso indebido a la aplicación",
        "alto",
    )
    assert (entry.detected_at, entry.simulated, entry.problems) == (DETECTED, False, [])
    assert (entry.last_record.seq, entry.last_record.kind.value) == (2, "updated")
    assert entry.last_record.recorded_at == RECORDED + timedelta(hours=1)
    keys = record_keys(world, "INC-2026-001")
    cited = [s.key for s in section.sources if s.kind == "incident" and s.key != "incidents/"]
    assert cited == keys
    for source in section.sources:
        assert source.sha256 == source_sha256(world.store, source.key)
    assert {s.at for s in section.sources if s.key in keys} == {
        RECORDED,
        RECORDED + timedelta(hours=1),
    }


def test_el_informe_no_reproduce_textos_libres(world):
    world.declare_levels()
    open_incident(world)
    world.update(analysis={"summary": FREE_TEXT}, severity="alto")
    world.update(actions=[{"kind": "other", "at": RECORDED, "description": "Aislar el servidor"}])

    daily = render_json(daily_report(world)).decode()
    month = render_json(monthly_at(world)).decode()

    for text in (daily, month):
        assert FREE_TEXT not in text
        assert "Aislar el servidor" not in text


def test_incidente_cerrado_no_figura_como_abierto_y_el_simulacro_se_marca(world):
    world.approve_procedure()
    world.closed_incident()
    open_incident(
        world,
        "SIM-2026-001",
        simulated=True,
        procedure=world.procedure().model_dump(mode="json"),
    )

    entries = daily_report(world).open_incidents.value

    assert [(e.id, e.simulated) for e in entries] == [("SIM-2026-001", True)]


def test_incidente_roto_se_muestra_con_su_error_y_sin_valores(world):
    open_incident(world, severity="alto")
    (manifest,) = scan_incident(world.store, "INC-2026-001").evidence.values()
    world.remove(manifest_key(manifest.id))

    section = daily_report(world).open_incidents

    (entry,) = section.value
    assert entry.id == "INC-2026-001"
    assert entry.problems and any("manifiesto" in p for p in entry.problems)
    assert (entry.title, entry.severity, entry.detected_at) == (None, None, None)
    assert (entry.simulated, entry.last_record) == (None, None)


def test_incidente_posterior_al_corte_no_existe_para_el_informe(world):
    open_incident(world)

    report = daily_report(world, generated_at=RECORDED - timedelta(hours=1))

    assert report.open_incidents.value == []


def test_mensual_separa_abiertos_cerrados_en_la_ventana_y_simulacros(world):
    world.approve_procedure()
    world.closed_incident()
    open_incident(
        world,
        "SIM-2026-001",
        simulated=True,
        procedure=world.procedure().model_dump(mode="json"),
    )
    open_incident(world, "INC-2026-002", severity="bajo")

    section = monthly_at(world).incidents

    counts = section.value.counts
    assert counts["abiertos"] == 1
    assert counts["cerrados en la ventana"] == 1
    assert counts["simulacros abiertos"] == 1
    assert counts["simulacros cerrados en la ventana"] == 0
    assert counts["historiales generados"] == 1
    assert counts["problemas de verificación"] == 0
    details = {e.subject: e.detail for e in section.value.items if "historial" not in e.detail}
    assert details["INC-2026-001"].startswith("cerrados en la ventana")
    assert details["INC-2026-002"].startswith("abiertos · severidad bajo")
    assert details["SIM-2026-001"].startswith("simulacros abiertos · severidad no declarada")
    assert any(e.detail.startswith("historial EV-") for e in section.value.items)
    assert section.value.coverage.partial
    for source in section.sources:
        assert source.sha256 == source_sha256(world.store, source.key)
    assert {"incident", "evidence"} <= {s.kind for s in section.sources}


def test_mensual_ignora_lo_cerrado_antes_de_la_ventana_y_lo_posterior(world):
    world.closed_incident()

    later = build_monthly(
        world.store,
        generated_at=datetime(2026, 12, 1, 5, 30, tzinfo=UTC),
        period="2026-11",
        tz=MADRID,
        checks=source_of(),
    )
    before = build_monthly(
        world.store,
        generated_at=datetime(2026, 10, 1, 5, 30, tzinfo=UTC),
        period="2026-09",
        tz=MADRID,
        checks=source_of(),
    )

    for report in (later, before):
        assert report.incidents.value.items == []
        assert report.incidents.value.counts["cerrados en la ventana"] == 0
        assert [s.key for s in report.incidents.sources] == ["incidents/"]


def test_mensual_lista_los_incidentes_rotos_aparte_y_sin_contarlos(world):
    open_incident(world)
    (manifest,) = scan_incident(world.store, "INC-2026-001").evidence.values()
    world.remove(manifest_key(manifest.id))

    value = monthly_at(world).incidents.value

    assert value.counts["abiertos"] == 0
    assert value.counts["problemas de verificación"] == 1
    assert value.items
    assert all(e.subject == "INC-2026-001" for e in value.items)
    assert all(e.detail.startswith("problema de verificación:") for e in value.items)
    assert "Acceso indebido" not in render_json(monthly_at(world)).decode()


def test_mensual_sin_incidentes_cita_el_prefijo_y_no_dice_cero_sin_fuente(world):
    section = monthly_at(world).incidents

    assert isinstance(section, Sourced)
    assert [s.key for s in section.sources] == ["incidents/"]
    assert section.value.items == []


def test_cierre_sin_historial_cuenta_como_roto(world):
    world.closed_incident()
    world.remove(manifest_key(scan_incident(world.store, "INC-2026-001").histories[0].id))

    value = monthly_at(world).incidents.value

    assert value.counts["cerrados en la ventana"] == 0
    assert value.counts["problemas de verificación"] == 1


def test_determinismo(world):
    world.closed_incident()
    open_incident(world, "INC-2026-002", severity="alto")

    first = (render_json(daily_report(world)), render_json(monthly_at(world)))
    second = (render_json(daily_report(world)), render_json(monthly_at(world)))

    assert first == second
    assert json.loads(first[0])["open_incidents"]["value"][0]["id"] == "INC-2026-002"


def test_registro_ilegible_es_unavailable_no_cero(world, monkeypatch):
    from ens_evidence.reports import daily
    from ens_evidence.workspace import WorkspaceError

    def broken(_store):
        raise WorkspaceError("sin acceso")

    monkeypatch.setattr(daily, "check_all_incidents", broken)

    assert isinstance(daily_report(world).open_incidents, Unavailable)
    assert isinstance(monthly_at(world).incidents, Unavailable)
