"""`scheduler status` con ciclos interrumpidos, informes incompletos o ilegibles y días sin informe.

Los informes salen del ciclo real (conector falso); los marcadores huérfanos y los ficheros
dañados se escriben a mano en el workspace. La orden solo lee: el workspace queda intacto.
"""

from datetime import UTC, datetime, timedelta

import pytest
from typer.testing import CliRunner

from ens_evidence.cli import app
from ens_evidence.reports.store import pairs, write_stage
from ens_evidence.scheduling.config import LoadedSchedule, ScheduleConfig
from ens_evidence.scheduling.scheduler import render_status, scheduler_status
from ens_evidence.workspace import LocalStore
from scheduling import test_cycle
from scheduling.test_cycle import TickClock

NOW = datetime(2026, 11, 5, 7, 0, tzinfo=UTC)  # 08:00 en Madrid, pasada la hora del diario
DEFAULT = LoadedSchedule(ScheduleConfig(), is_default=True)
ORPHAN_COLLECTED = "20261104T050000-aaaaaa"
ORPHAN_STARTED = "20261104T060000-bbbbbb"
UNREADABLE = "20261104T070000-cccccc"


def publish_day(store, settings, env, context, plugins, day: int) -> None:
    start = datetime(2026, 11, day, 5, 2, tzinfo=UTC)
    outcome = test_cycle.cycle(
        store, settings, env, context, plugins, TickClock(start), period=f"2026-11-{day:02d}"
    )
    assert outcome.completed


@pytest.fixture
def messy_workspace(store, settings, env, context, plugins, fake, restic):
    """Informes publicados los días 1, 3 y 4 (el 4 sin HTML), el 2 ilegible y el 5 pendiente;
    tres ciclos que nunca terminaron (dos legibles y uno con marcadores dañados)."""
    fake("ok")
    for day in (1, 3, 4):
        publish_day(store, settings, env, context, plugins, day)
    (store._root / pairs(store, "daily", "2026-11-04")[0].html_key).unlink()
    store.create_bytes("evidence/reports/daily/2026-11-02/20261102T050200000000Z.json", b"{roto")
    write_stage(
        store, ORPHAN_COLLECTED, "started", at=NOW - timedelta(hours=2), period="2026-11-04"
    )
    write_stage(
        store, ORPHAN_COLLECTED, "collected", at=NOW - timedelta(hours=2), period="2026-11-04"
    )
    write_stage(store, ORPHAN_STARTED, "started", at=NOW - timedelta(hours=1), period="2026-11-04")
    store.create_bytes(f"evidence/reports/cycles/{UNREADABLE}.started.json", b"no es json")
    return store


def snapshot(store) -> dict[str, bytes]:
    return {key: store.read_bytes(key) for key in store.list("") if key}


def test_status_lista_cada_ciclo_interrumpido_con_su_ultima_etapa_y_los_ilegibles(messy_workspace):
    status = scheduler_status(messy_workspace, DEFAULT, NOW)
    text = render_status(status)

    assert {(run.run_id, run.last_stage) for run in status.interrupted} == {
        (ORPHAN_COLLECTED, "collected"),
        (ORPHAN_STARTED, "started"),
    }
    assert status.unreadable_cycles == (UNREADABLE,)
    assert "Ciclos interrumpidos: 3" in text
    assert f"{ORPHAN_COLLECTED}: última etapa collected" in text
    assert f"{ORPHAN_STARTED}: última etapa started" in text
    assert f"{UNREADABLE}: marcadores ilegibles" in text


def test_status_cuenta_como_dia_sin_informe_el_ilegible_y_el_de_hoy_pendiente(messy_workspace):
    status = scheduler_status(messy_workspace, DEFAULT, NOW)

    assert status.month == "2026-11"
    assert "2026-11-02" in status.days_without_report  # JSON ilegible
    assert "2026-11-05" in status.days_without_report  # vencido y sin ciclo
    assert not {"2026-11-01", "2026-11-03"} & set(status.days_without_report)
    assert "Días sin informe diario en 2026-11:" in render_status(status)


def test_status_marca_el_ultimo_informe_incompleto_si_falta_el_html(messy_workspace):
    status = scheduler_status(messy_workspace, DEFAULT, NOW)

    last = status.last_reports["daily"]
    assert last is not None and last.period == "2026-11-04" and not last.published
    assert "(incompleto: falta el HTML)" in render_status(status)


def test_status_no_escribe_nada_en_el_workspace(messy_workspace):
    before = snapshot(messy_workspace)

    render_status(scheduler_status(messy_workspace, DEFAULT, NOW))

    assert snapshot(messy_workspace) == before


def test_cli_scheduler_status_muestra_interrumpidos_y_dias_sin_informe(
    messy_workspace, settings_path, monkeypatch
):
    monkeypatch.setattr("ens_evidence.cli.clock", lambda: NOW)
    before = snapshot(messy_workspace)

    result = CliRunner().invoke(app, ["--settings", str(settings_path), "scheduler", "status"])

    assert result.exit_code == 0, result.output
    assert "Ciclos interrumpidos: 3" in result.output
    assert f"{UNREADABLE}: marcadores ilegibles" in result.output
    assert "  2026-11-02\n" in result.output and "  2026-11-04 a 2026-11-05\n" in result.output
    assert snapshot(messy_workspace) == before


def test_status_rotula_en_castellano_y_agrupa_los_dias_seguidos():
    from ens_evidence.scheduling.scheduler import _day_ranges

    assert _day_ranges(("2026-10-01", "2026-10-02", "2026-10-03", "2026-10-07", "2026-10-09")) == [
        "2026-10-01 a 2026-10-03",
        "2026-10-07",
        "2026-10-09",
    ]
    assert _day_ranges(("2026-10-31", "2026-11-01")) == ["2026-10-31 a 2026-11-01"]
    assert _day_ranges(()) == []


def test_status_del_mensual_pendiente_nombra_el_mes_del_informe_no_el_del_disparo(tmp_path):
    first = datetime(2026, 11, 1, 7, 0, tzinfo=UTC)  # pasada la hora del mensual, sin informes

    text = render_status(scheduler_status(LocalStore(tmp_path / "ws"), DEFAULT, first))

    assert "mensual:" in text
    assert "pendiente ahora: informe de 2026-10," in text
    assert "pendiente ahora: 2026-11," not in text
