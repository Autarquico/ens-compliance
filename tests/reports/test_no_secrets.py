"""Un collector que filtra secretos no los deja en informe, evidencia, marcadores ni logs."""

import logging

from ens_evidence.reports.store import run_ids, stages_of

from .acceptance_helpers import (
    LEAK,
    TOKEN,
    cycle_world,
    daily_html,
    daily_json,
    everything_stored,
)

MODES = {"github": "raise", "cloudflare": "leak", "google-workspace": "ok"}


def test_ningun_valor_secreto_llega_a_informe_evidencia_marcadores_ni_logs(
    tmp_path, monkeypatch, capsys, caplog
):
    caplog.set_level(logging.DEBUG)
    world = cycle_world(tmp_path, monkeypatch, MODES)

    cycle = world.run("report", "daily", "--cycle")
    html = world.run("report", "daily", "--stdout", "--format", "html")
    json_out = world.run("report", "daily", "--stdout")
    monthly = world.run("report", "monthly", "--stdout", "--format", "html")

    assert cycle.exit_code == 3, cycle.output
    stored = everything_stored(world.store)
    (run_id,) = run_ids(world.store)
    markers = " ".join(str(m) for m in stages_of(world.store, run_id).stages)
    captured = capsys.readouterr()
    surfaces = {
        "workspace completo": stored,
        "informe html": daily_html(world.store),
        "informe json": str(daily_json(world.store)),
        "marcadores": markers,
        "salida del ciclo": cycle.output,
        "stdout html": html.output,
        "stdout json": json_out.output,
        "mensual": monthly.output,
        "capsys": captured.out + captured.err,
        "caplog": caplog.text,
    }
    for name, text in surfaces.items():
        assert TOKEN not in text, name
        assert LEAK not in text, name


def test_los_dos_fallos_quedan_registrados_sin_el_valor(tmp_path, monkeypatch):
    world = cycle_world(tmp_path, monkeypatch, MODES)

    world.run("report", "daily", "--cycle")

    stored = everything_stored(world.store)
    assert "collect_crashed:RuntimeError" in stored
    assert "secret_detected:detail" in stored
    attempts = {
        a["connector"]: a["last_status"]
        for a in daily_json(world.store)["cycle"]["connector_attempts"]
    }
    assert attempts["github"] == attempts["cloudflare"] == "ERROR"
    assert attempts["google-workspace"] == "SUCCESS"
