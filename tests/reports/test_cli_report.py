"""`report daily|monthly` y `scheduler init-config` por la CLI."""

import json
from datetime import timedelta

import pytest
import yaml
from checks.test_engine import NOW, collect
from typer.testing import CliRunner

from ens_evidence.checks.engine import run_all
from ens_evidence.cli import app
from ens_evidence.models.enums import CollectionStatus
from ens_evidence.reports.model import DailyReport
from ens_evidence.reports.store import (
    cycle_finished,
    load_json_strict,
    pairs,
    run_ids,
    stages_of,
    write_stage,
)
from ens_evidence.scheduling.config import SCHEDULE_KEY, ScheduleConfig, load_schedule_config
from ens_evidence.workspace.cycle_lock import CycleLock
from ens_evidence.workspace.settings import load_settings

TODAY = NOW + timedelta(hours=18)  # 2026-10-31 06:00 UTC: 07:00 en Madrid
PERIOD = "2026-10-31"


@pytest.fixture
def cli(settings_path, plugins, fake, monkeypatch):
    monkeypatch.setattr("ens_evidence.cli.clock", lambda: TODAY)
    monkeypatch.setattr("ens_evidence.cli.load_plugins", lambda: plugins)

    def run(*args: str):
        return CliRunner().invoke(app, ["--settings", str(settings_path), *args])

    return run


@pytest.fixture
def checked(store):
    collect(store)
    run_all(store, NOW)


def report_keys(store, prefix="evidence/reports/"):
    return [k for k in store.list("evidence/reports") if k.startswith(prefix)]


# --- sin --cycle: el estado actual, sin red ni plugins -----------------------------------------


def test_report_daily_sin_cycle_no_usa_red_ni_plugins_y_escribe_el_par(
    cli, store, checked, offline, monkeypatch
):
    def no_plugins():
        raise AssertionError("report sin --cycle no carga plugins")

    monkeypatch.setattr("ens_evidence.cli.load_plugins", no_plugins)

    result = cli("report", "daily")

    assert result.exit_code == 0, result.output
    (pair,) = pairs(store, "daily", PERIOD)
    assert pair.published and pair.json_key in result.output
    report = DailyReport.model_validate(load_json_strict(store.read_bytes(pair.json_key)))
    assert (report.cycle, report.period, report.generated_at) == (None, PERIOD, TODAY)
    assert run_ids(store) == []


def test_report_daily_con_conector_sin_ejecucion_de_checks_escribe_y_sale_con_3(cli, store):
    collect(store)

    result = cli("report", "daily")

    assert result.exit_code == 3
    assert pairs(store, "daily", PERIOD)[0].published


def test_report_daily_con_recogida_en_error_escribe_y_sale_con_3(cli, store):
    collect(store, collection_status=CollectionStatus.ERROR, data={"error": "api_unavailable"})
    run_all(store, NOW)

    result = cli("report", "daily")

    assert result.exit_code == 3
    assert pairs(store, "daily", PERIOD)[0].published


def test_stdout_no_escribe_nada_y_format_elige_json_o_html(cli, store, checked):
    as_json = cli("report", "daily", "--stdout")
    as_html = cli("report", "daily", "--stdout", "--format", "html")

    assert as_json.exit_code == as_html.exit_code == 0
    assert json.loads(as_json.output)["period"] == PERIOD
    assert as_html.output.lstrip().startswith("<!DOCTYPE html>")
    assert report_keys(store) == []


def test_format_sin_stdout_se_rechaza_sin_escribir(cli, store, checked):
    result = cli("report", "daily", "--format", "html")

    assert result.exit_code == 2
    assert report_keys(store) == []


def test_at_renderiza_el_estado_en_ese_instante_y_no_escribe_checks(cli, store, checked):
    before = set(store.list("evidence/checks"))
    at = "2026-10-30T20:00:00+00:00"

    result = cli("report", "daily", "--stdout", "--at", at)

    assert result.exit_code == 0, result.output
    document = json.loads(result.output)
    assert (document["period"], document["generated_at"]) == ("2026-10-30", "2026-10-30T20:00:00Z")
    assert set(store.list("evidence/checks")) == before


def test_at_sin_stdout_se_rechaza_para_no_rellenar_dias_pasados(cli, store, checked):
    result = cli("report", "daily", "--at", "2026-10-30T20:00:00+00:00")

    assert result.exit_code == 2
    assert "--stdout" in result.output
    assert report_keys(store) == []


def test_at_invalido_se_rechaza(cli, store, checked):
    result = cli("report", "daily", "--at", "ayer")

    assert result.exit_code == 2
    assert report_keys(store) == []


def test_report_monthly_cubre_el_mes_natural_anterior(cli, store, checked):
    result = cli("report", "monthly")

    assert result.exit_code == 0, result.output
    (pair,) = pairs(store, "monthly", "2026-09")
    assert pair.published and pair.json_key in result.output


# --- con --cycle -------------------------------------------------------------------------------


def test_at_con_cycle_se_rechaza_sin_tomar_el_lock_ni_escribir(cli, store, settings_path):
    result = cli("report", "daily", "--cycle", "--at", "2026-10-30T20:00:00+00:00")

    assert result.exit_code == 2
    assert report_keys(store) == [] and run_ids(store) == []


def test_con_el_lock_ocupado_sale_con_2_sin_escribir_nada(cli, store, settings_path):
    settings = load_settings(settings_path, allow_inside_product=True)
    lock = CycleLock.for_settings(settings).acquire()
    try:
        result = cli("report", "daily", "--cycle")
    finally:
        lock.release()

    assert result.exit_code == 2
    assert "ciclo en curso" in result.output
    assert report_keys(store) == [] and run_ids(store) == []


def only_stored_evidence(store):
    """Sin conectores que recoger: el ciclo trabaja con la evidencia que ya hay."""
    store.write_text(SCHEDULE_KEY, yaml.safe_dump({"collect": {"connectors": []}}))


def test_report_daily_con_cycle_evalua_informa_y_registra_la_copia_fallida(cli, store, checked):
    only_stored_evidence(store)

    result = cli("report", "daily", "--cycle")

    assert result.exit_code == 0, result.output
    (run_id,) = run_ids(store)
    assert [m.stage for m in stages_of(store, run_id).stages] == [
        "started",
        "collected",
        "checked",
        "reported",
        "backup_failed",
    ]
    assert cycle_finished(store, run_id)
    assert "La copia ha fallado" in result.output
    report = DailyReport.model_validate(
        load_json_strict(store.read_bytes(pairs(store, "daily", PERIOD)[0].json_key))
    )
    assert (report.cycle.run_id, report.cycle.trigger) == (run_id, "manual")


def test_reanudar_un_run_con_reported_y_sin_copia_imprime_el_informe_y_hace_la_copia(
    cli, store, checked
):
    """La caída llegó durante la copia: `reported` está y falta la etapa final."""
    only_stored_evidence(store)
    assert cli("report", "daily", "--cycle").exit_code == 0
    (published,) = pairs(store, "daily", PERIOD)
    run_id = "20261031T060500-abcdef"
    for stage in ("started", "collected", "checked"):
        write_stage(store, run_id, stage, at=TODAY, period=PERIOD)
    write_stage(store, run_id, "reported", at=TODAY, period=PERIOD, reference=published.json_key)

    result = cli("report", "daily", "--cycle")

    assert result.exit_code == 0, result.output
    assert published.json_key in result.output
    assert stages_of(store, run_id).stages[-1].stage == "backup_failed"
    assert len(pairs(store, "daily", PERIOD)) == 1


def test_con_cycle_una_recogida_que_deja_el_conector_en_error_sale_con_3(cli, store, fake):
    fake("ok")  # el conector falso no devuelve el contenido de un GitHub real: los checks dan ERROR

    result = cli("report", "daily", "--cycle")

    assert result.exit_code == 3, result.output
    assert pairs(store, "daily", PERIOD)[0].published


def test_report_monthly_con_cycle_publica_solo_el_mensual(cli, store, checked):
    only_stored_evidence(store)

    result = cli("report", "monthly", "--cycle")

    assert result.exit_code == 0, result.output
    assert not pairs(store, "daily", PERIOD)
    assert pairs(store, "monthly", "2026-09")[0].published


# --- scheduler init-config -------------------------------------------------------------------


def test_init_config_escribe_los_valores_por_defecto_y_no_pisa_un_fichero_existente(cli, store):
    first = cli("scheduler", "init-config")

    assert first.exit_code == 0, first.output
    loaded = load_schedule_config(store)
    assert loaded.config == ScheduleConfig() and not loaded.is_default

    store.write_text(SCHEDULE_KEY, yaml.safe_dump({"timezone": "Europe/Lisbon"}))
    second = cli("scheduler", "init-config")

    assert second.exit_code == 0 and "ya existe" in second.output
    assert load_schedule_config(store).config.timezone == "Europe/Lisbon"
