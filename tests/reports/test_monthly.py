import json
from datetime import UTC, datetime, timedelta

import pytest
import yaml

from ens_evidence.assessment.render import HUMAN_REVIEW_PENDING, NO_CONFORMITY
from ens_evidence.backup.records import BackupRun, dump, run_key
from ens_evidence.backup.restore_test import RestoreTest, restore_test_key
from ens_evidence.connectors.config import connector_config_key
from ens_evidence.evidence.importer import import_manual
from ens_evidence.models.enums import CheckStatus
from ens_evidence.reports.labels import REPORT_NOT_A_REVIEW
from ens_evidence.reports.model import (
    ChangeItem,
    Changes,
    Sourced,
    Unavailable,
    render_json,
)
from ens_evidence.reports.monthly import build_monthly, month_window, previous_month
from ens_evidence.scope import APPROVAL_KEY as SCOPE_APPROVAL_KEY
from ens_evidence.workspace import LocalStore

from .helpers import SHA, daily, put_daily, put_monthly, ref, source_sha256
from .test_daily import walk
from .world import MADRID, check_result, source_of, static_run

GENERATED = datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
PERIOD = "2026-10"
AGGREGATES = (
    "inventory changes deviations evidence incidents vulnerabilities backups restore_tests "
    "access_reviews pending corrective_actions daily_reports"
).split()
ASSET = (
    "  - {{id: {id}, type: laptop, deployment: deployed, os: macOS 15, owner: Ana, "
    "encrypted: true, usage: trabajo}}\n"
)


@pytest.fixture
def store(tmp_path):
    return LocalStore(tmp_path / "ws")


def monthly_at(store, *, period=PERIOD, generated_at=GENERATED, checks=None):
    return build_monthly(
        store,
        generated_at=generated_at,
        period=period,
        tz=MADRID,
        checks=checks or source_of(),
    )


def write_assets(store, *ids):
    store.write_text("scope/assets.yaml", "assets:\n" + "".join(ASSET.format(id=i) for i in ids))


def day_report(store, day: int, month: int = 10, **overrides):
    period = f"2026-{month:02d}-{day:02d}"
    at = datetime(2026, month, day, 5, 30, tzinfo=UTC)
    return put_daily(store, daily(period, generated_at=at, **overrides), at)


def fill_month(store, *, skip=()):
    for day in range(1, 32):
        if day not in skip:
            day_report(store, day)


def sourced(report):
    return [n for n in AGGREGATES if isinstance(getattr(report, n), Sourced)]


# --- ventana ------------------------------------------------------------------------------------


def test_la_ventana_es_el_mes_local_guardado_en_utc():
    window = month_window("2026-10", MADRID)

    assert window.start == datetime(2026, 9, 30, 22, tzinfo=UTC)
    assert window.end == datetime(2026, 10, 31, 23, tzinfo=UTC)
    assert window.start.utcoffset() == timedelta(0)


def test_el_cambio_de_hora_dentro_del_mes_alarga_o_acorta_la_ventana():
    october = month_window("2026-10", MADRID)  # termina el horario de verano: 25 h el día 25
    march = month_window("2026-03", MADRID)  # empieza el horario de verano: 23 h el día 29

    assert october.end - october.start == timedelta(days=31, hours=1)
    assert march.end - march.start == timedelta(days=31) - timedelta(hours=1)
    assert march.start == datetime(2026, 2, 28, 23, tzinfo=UTC)


def test_el_mes_anterior_se_calcula_a_traves_del_ano():
    assert previous_month("2026-01") == "2025-12"
    assert previous_month("2026-03") == "2026-02"


def test_ventana_en_el_informe(store):
    report = monthly_at(store)

    assert report.period == PERIOD
    assert report.window == month_window(PERIOD, MADRID)


# --- cobertura ----------------------------------------------------------------------------------


def test_mes_completo_no_es_parcial(store):
    fill_month(store)
    write_assets(store, "a1")

    report = monthly_at(store)

    assert sourced(report)
    for name in sourced(report):
        coverage = getattr(report, name).value.coverage
        assert (coverage.days_with_report, coverage.days_in_period, coverage.partial) == (
            31,
            31,
            False,
        )
    assert not any("parcial" in n for n in report.notices)


def test_mes_con_huecos_marca_partial_en_cada_agregado_y_avisa(store):
    fill_month(store, skip={3, 4, 17})
    write_assets(store, "a1")

    report = monthly_at(store)

    assert sourced(report)
    for name in sourced(report):
        coverage = getattr(report, name).value.coverage
        assert (coverage.days_with_report, coverage.partial) == (28, True)
    assert any("28 de 31" in n for n in report.notices)
    missing = {e.subject for e in report.daily_reports.value.items}
    assert {"2026-10-03", "2026-10-04", "2026-10-17"} <= missing
    assert report.daily_reports.value.counts["días sin informe"] == 3


def test_sin_ningun_informe_diario_es_unavailable_no_cero(store):
    report = monthly_at(store)

    assert isinstance(report.daily_reports, Unavailable)
    assert "0 de 31" in report.daily_reports.reason


def test_informes_diarios_corruptos_no_cuentan_y_se_listan(store):
    fill_month(store, skip={5, 6})
    pair = day_report(store, 5)
    (store._root / pair.json_key).write_bytes(b"{")
    other = day_report(store, 6)
    (store._root / other.html_key).write_bytes(b"")

    report = monthly_at(store)

    value = report.daily_reports.value
    assert value.coverage.days_with_report == 29
    assert value.coverage.partial
    assert value.counts["informes ilegibles"] == 2
    assert any(pair.json_key in e.detail for e in value.items)
    assert any("ilegibles" in n for n in report.notices)


def test_varios_informes_el_mismo_dia_se_listan(store):
    fill_month(store)
    later = datetime(2026, 10, 9, 18, tzinfo=UTC)
    put_daily(store, daily("2026-10-09", generated_at=later), later)

    value = monthly_at(store).daily_reports.value

    assert value.coverage.days_with_report == 31
    assert any(e.subject == "2026-10-09" and "varios informes" in e.detail for e in value.items)


# --- inventario y cambios -----------------------------------------------------------------------


def test_inventario_cuenta_activos_y_cita_su_fuente(store):
    write_assets(store, "a1", "a2")

    section = monthly_at(store).inventory

    assert section.value.counts["total"] == 2
    assert [e.subject for e in section.value.items] == ["a1", "a2"]
    assert [(s.kind, s.key) for s in section.sources] == [("inventory", "scope/assets.yaml")]


def test_inventario_ausente_es_unavailable(store):
    assert isinstance(monthly_at(store).inventory, Unavailable)


def publish_previous(store, *ids):
    write_assets(store, *ids)
    previous = monthly_at(store, period="2026-09", generated_at=GENERATED - timedelta(days=31))
    put_monthly(store, previous, GENERATED - timedelta(days=30), b"<html>x</html>")


def test_altas_y_bajas_frente_al_mensual_anterior(store):
    fill_month(store)
    publish_previous(store, "a1", "a2")
    write_assets(store, "a2", "a3")

    changes = monthly_at(store).changes

    items = {(e.subject, e.detail.split(" ")[0]) for e in changes.value.items}
    assert {("a3", "alta"), ("a1", "baja")} <= items
    assert ("a2", "alta") not in items and ("a2", "baja") not in items
    assert (changes.value.counts["altas"], changes.value.counts["bajas"]) == (1, 1)
    assert "report" in {s.kind for s in changes.sources}


def test_sin_mensual_anterior_las_altas_y_bajas_no_se_calculan_ni_se_dan_por_cero(store):
    day_report(store, 2, changes=daily_changes())
    write_assets(store, "a1")

    changes = monthly_at(store).changes

    assert "altas" not in changes.value.counts
    (entry,) = [e for e in changes.value.items if e.subject == "inventario"]
    assert "no calculables" in entry.detail


def test_mensual_anterior_editado_tras_publicarse_no_es_base(store):
    publish_previous(store, "a1")
    (key,) = [k for k in store.list("evidence/reports/monthly/2026-09") if k.endswith(".json")]
    (store._root / key).write_bytes((store._root / key).read_bytes() + b" ")
    write_assets(store, "a1", "a2")

    changes = monthly_at(store).changes

    assert isinstance(changes, Unavailable)  # ni informes diarios ni base


def test_mensual_anterior_corrupto_no_es_base(store):
    publish_previous(store, "a1")
    (pair,) = [p for p in store.list("evidence/reports/monthly/2026-09") if p.endswith(".json")]
    (store._root / pair).write_bytes(b"{")
    write_assets(store, "a1", "a2")

    changes = monthly_at(store).changes

    assert isinstance(changes, Unavailable)  # ni informes diarios ni base


def daily_changes():
    item = ChangeItem(subject="readiness", change="changed")
    return Sourced[Changes](value=Changes(base_period="2026-10-01", items=[item]), sources=[ref()])


def test_cambios_de_los_informes_diarios_entran_con_su_dia(store):
    day_report(store, 2, changes=daily_changes())

    section = monthly_at(store).changes

    assert section.value.counts["cambios en informes diarios"] == 1
    assert any(e.subject == "readiness" and "2026-10-02" in e.detail for e in section.value.items)


# --- plan de tratamiento ------------------------------------------------------------------------

PLAN = (
    "actions:\n"
    "  - {id: A1, risk: R1, description: d, owner: o, due: 2026-10-15, status: pending}\n"
    "  - {id: A2, risk: R1, description: d, owner: o, due: 2026-12-01, status: in_progress}\n"
    "  - {id: A3, risk: R2, description: d, owner: o, due: 2026-09-01, status: done}\n"
)


def test_plan_de_tratamiento_rotulado_y_con_acciones_vencidas(store):
    store.write_text("risk/treatment-plan.yaml", PLAN)

    section = monthly_at(store).corrective_actions

    assert section.value.label == "plan de tratamiento de riesgos"
    assert section.value.counts["vencidas"] == 1
    details = {e.subject: e.detail for e in section.value.items}
    assert "VENCIDA" in details["A1"]
    assert "VENCIDA" not in details["A2"] and "VENCIDA" not in details["A3"]
    assert section.sources[0].key == "risk/treatment-plan.yaml"


def test_plan_de_tratamiento_ausente_o_ilegible_es_unavailable_con_el_rotulo(store):
    assert "plan de tratamiento de riesgos" in monthly_at(store).corrective_actions.reason
    store.write_text("risk/treatment-plan.yaml", "actions: 3\n")
    assert "ilegible" in monthly_at(store).corrective_actions.reason


# --- copias y pruebas de restauración -----------------------------------------------------------


def backup_run(store, run_id, started):
    run = BackupRun(
        run_id=run_id,
        status="completed",
        started_at=started,
        finished_at=started + timedelta(minutes=5),
        repository="r",
        snapshot_id="s" * 8,
        inventory={},
    )
    store.write_bytes(run_key(run_id), dump(run))


def restore_test(test_id, performed):
    return RestoreTest.model_validate(
        {
            "test_id": test_id,
            "system": "svc",
            "performed_at": performed,
            "restore_id": "R1",
            "isolated_target": True,
            "backup": {"snapshot_id": SHA},
            "result": {"restored": True, "integrity_verified": True, "application_started": True},
            "review": {
                "approved": True,
                "reviewer": "Ana",
                "role": "system_administrator",
                "reviewed_at": performed,
                "valid_until": performed + timedelta(days=90),
            },
            "repository_id": SHA,
        }
    )


def write_restore_test(store, test_id, performed):
    store.write_text(
        restore_test_key(test_id),
        yaml.safe_dump(restore_test(test_id, performed).model_dump(mode="json")),
    )


def test_copias_de_la_ventana_con_los_limites_en_utc_del_mes_local(store):
    start, end = month_window(PERIOD, MADRID).start, month_window(PERIOD, MADRID).end
    backup_run(store, "b-before", start - timedelta(seconds=1))
    backup_run(store, "b-first", start)
    backup_run(store, "b-last", end - timedelta(seconds=1))
    backup_run(store, "b-after", end)

    section = monthly_at(store).backups

    assert [e.subject for e in section.value.items] == ["b-first", "b-last"]
    assert section.value.counts["total"] == 2
    assert {s.kind for s in section.sources} == {"backup_run"}


def test_sin_copias_en_la_ventana_es_unavailable_no_cero(store):
    backup_run(store, "b-old", datetime(2026, 9, 1, tzinfo=UTC))

    assert isinstance(monthly_at(store).backups, Unavailable)


def test_pruebas_de_restauracion_de_la_ventana(store):
    write_restore_test(store, "RT-in", datetime(2026, 10, 12, 9, tzinfo=UTC))
    write_restore_test(store, "RT-out", datetime(2026, 11, 3, 9, tzinfo=UTC))
    store.write_text(restore_test_key("RT-bad"), "test_id: 3\n")

    section = monthly_at(store).restore_tests

    assert [e.subject for e in section.value.items] == ["RT-in (svc)"]
    assert section.value.counts["registros ilegibles"] == 1
    assert section.sources[0].kind == "restore_test"


def test_ultima_hora_local_del_mes_entra_y_la_primera_del_siguiente_no(store):
    write_restore_test(store, "RT-last", datetime(2026, 10, 31, 22, 59, tzinfo=UTC))  # 23:59 local
    write_restore_test(store, "RT-next", datetime(2026, 10, 31, 23, 0, tzinfo=UTC))  # 00:00 local

    items = monthly_at(store).restore_tests.value.items

    assert [e.subject for e in items] == ["RT-last (svc)"]


# --- desviaciones y evidencia -------------------------------------------------------------------


def test_desviaciones_del_ultimo_intento_al_cierre_del_mes(store):
    end = month_window(PERIOD, MADRID).end
    run = static_run(
        store, "github", 1, end - timedelta(days=2),
        check_result(CheckStatus.FAIL, subject="repo-a"), check_result(CheckStatus.PASS),
    )  # fmt: skip
    later = static_run(store, "github", 2, end + timedelta(days=1), check_result(CheckStatus.FAIL))

    section = monthly_at(store, checks=source_of(run, later)).deviations

    assert [(e.subject, e.detail) for e in section.value.items] == [
        ("github: github.x repo-a", "FAIL")
    ]
    assert section.value.counts["FAIL"] == 1 and section.value.counts["PASS"] == 1
    assert section.sources[0].key == run.run_id


def test_sin_ejecucion_de_checks_las_desviaciones_son_unavailable(store):
    assert isinstance(monthly_at(store).deviations, Unavailable)


def test_evidencia_de_la_ventana(store):
    inside = datetime(2026, 10, 10, 8, tzinfo=UTC)
    for name, when in (("in", inside), ("out", inside + timedelta(days=40))):
        import_manual(
            store, name.encode(), asset_id="svc", submitted_by="Ana", now=when, collected_at=when,
            valid_until=None,
        )  # fmt: skip

    section = monthly_at(store).evidence

    assert section.value.counts["total"] == 1
    assert section.sources[0].kind == "evidence"


# --- secciones no disponibles, determinismo y trazabilidad --------------------------------------


@pytest.mark.parametrize("name", ["vulnerabilities", "access_reviews"])
def test_secciones_sin_registro_son_unavailable_con_motivo(store, name):
    fill_month(store)
    write_assets(store, "a1")

    section = getattr(monthly_at(store), name)

    assert isinstance(section, Unavailable)
    assert section.reason


def test_mismo_workspace_y_mismos_argumentos_dan_los_mismos_bytes(store):
    fill_month(store, skip={2})
    write_assets(store, "a1")
    store.write_text("risk/treatment-plan.yaml", PLAN)
    backup_run(store, "b1", datetime(2026, 10, 5, tzinfo=UTC))

    first = render_json(monthly_at(store))
    second = render_json(monthly_at(store))

    assert first == second


def test_cada_dato_tiene_fuentes_reales_o_es_unavailable(store):
    fill_month(store)
    write_assets(store, "a1")
    store.write_text("risk/treatment-plan.yaml", PLAN)
    backup_run(store, "b1", datetime(2026, 10, 5, tzinfo=UTC))
    write_restore_test(store, "RT-1", datetime(2026, 10, 12, 9, tzinfo=UTC))
    report = monthly_at(store)

    found = list(walk(json.loads(render_json(report))))

    names = {path.split(".")[1] for path, _ in found}
    assert set(AGGREGATES) <= names
    sourced_count = 0
    for path, node in found:
        if "reason" in node:
            assert node["reason"], path
            continue
        sourced_count += 1
        assert node["sources"], path
        for source in node["sources"]:
            real = source_sha256(store, source["key"])
            assert real == source["sha256"], (path, source["key"])
    assert sourced_count >= 6


def test_workspace_vacio_no_presenta_nada_como_cero(store):
    report = monthly_at(store)

    for name in set(AGGREGATES) - {"incidents", "restore_tests"}:
        section = getattr(report, name)
        assert isinstance(section, Unavailable), name
    assert [s.key for s in report.incidents.sources] == ["incidents/"]
    assert [s.kind for s in report.restore_tests.sources] == ["restore_test"]


def test_el_informe_no_declara_cumplimiento(store):
    fill_month(store)
    text = render_json(monthly_at(store)).decode().lower()

    assert NO_CONFORMITY in monthly_at(store).notices
    assert "cumple" not in text.replace("no declara conformidad", "")
    assert "compliant" not in text


# --- ronda de arreglos: rótulos llanos, huecos y ausencias ---------------------------------------


def test_copias_con_rotulos_llanos_y_sin_codigos(store):
    backup_run(store, "b1", datetime(2026, 10, 5, tzinfo=UTC))

    value = monthly_at(store).backups.value

    assert value.counts["copia completada"] == 1
    assert value.items[0].detail.startswith("copia completada · inicio")
    assert not any("estado " in k for k in value.counts)


def test_inventario_con_el_tipo_de_activo_en_castellano(store):
    write_assets(store, "a1")

    value = monthly_at(store).inventory.value

    assert value.counts["tipo portátil"] == 1
    assert value.items[0].detail == "portátil"


def test_el_mensual_lleva_un_unico_aviso_de_revision_humana(store):
    notices = monthly_at(store).notices

    assert HUMAN_REVIEW_PENDING in notices
    assert REPORT_NOT_A_REVIEW not in notices
    assert sum("revisión" in n.lower() for n in notices) == 1


def test_cero_pruebas_de_restauracion_es_un_cero_citando_lo_consultado(store):
    write_restore_test(store, "RT-old", datetime(2026, 9, 12, 9, tzinfo=UTC))

    section = monthly_at(store).restore_tests

    assert section.value.counts["total"] == 0
    assert [(s.kind, s.key) for s in section.sources] == [
        ("restore_test", "evidence/restore-tests/")
    ]
    assert section.sources[0].sha256 == source_sha256(store, "evidence/restore-tests/")


def test_sin_pruebas_en_el_mes_va_a_pendientes_como_pendiente(store):
    store.write_text(SCOPE_APPROVAL_KEY, "x: 1\n")

    pending = monthly_at(store).pending.value

    assert pending.counts["pruebas de restauración sin hacer en el mes"] == 1
    entry = next(e for e in pending.items if e.subject == "pruebas de restauración")
    assert "pendiente" in entry.detail


def test_con_una_prueba_en_el_mes_no_hay_pendiente_de_restauracion(store):
    store.write_text(SCOPE_APPROVAL_KEY, "x: 1\n")
    write_restore_test(store, "RT-in", datetime(2026, 10, 12, 9, tzinfo=UTC))

    pending = monthly_at(store).pending.value

    assert pending.counts["pruebas de restauración sin hacer en el mes"] == 0
    assert all(e.subject != "pruebas de restauración" for e in pending.items)


def test_conector_configurado_sin_ejecucion_se_lista_y_no_se_omite(store):
    end = month_window(PERIOD, MADRID).end
    run = static_run(store, "github", 1, end - timedelta(days=2), check_result(CheckStatus.PASS))
    store.write_text(connector_config_key("cloudflare"), "x: 1\n")

    value = monthly_at(store, checks=source_of(run)).deviations.value

    assert value.counts["conectores sin datos"] == 1
    assert any(
        e.subject == "cloudflare" and "sin ejecución utilizable" in e.detail for e in value.items
    )


def test_conector_sin_configurar_se_cuenta_aparte(store):
    end = month_window(PERIOD, MADRID).end
    run = static_run(store, "github", 1, end - timedelta(days=2), check_result(CheckStatus.PASS))

    value = monthly_at(store, checks=source_of(run)).deviations.value

    assert value.counts["conectores sin datos"] == 0
    assert value.counts["conectores sin configurar"] >= 1


def test_dias_sin_cambios_calculables_no_publican_cero_cambios(store):
    write_assets(store, "a1")
    publish_previous(store, "a1")
    day_report(store, 2)  # el diario no pudo calcular sus cambios (Unavailable)

    value = monthly_at(store).changes.value

    assert "cambios en informes diarios" not in value.counts
    assert any(
        e.subject == "informes diarios" and "no calculables" in e.detail for e in value.items
    )


def test_cambios_con_un_solo_diario_explica_que_hace_falta_otro(store):
    day_report(store, 2)

    reason = monthly_at(store).changes.reason

    assert "al menos dos" in reason
    assert "sin informes diarios" not in reason


def test_un_diario_editado_tras_publicarse_es_un_dia_ilegible_nunca_cubierto(store):
    fill_month(store, skip={7})
    pair = day_report(store, 7)
    path = store._root / pair.json_key
    path.write_bytes(path.read_bytes() + b" ")

    report = monthly_at(store)

    value = report.daily_reports.value
    assert value.coverage.days_with_report == 30
    assert {"días sin informe": 1, "informes ilegibles": 1}.items() <= value.counts.items()
    assert any(
        e.subject == "informe diario ilegible" and pair.json_key in e.detail for e in value.items
    )
    assert any("ilegibles" in n for n in report.notices)
