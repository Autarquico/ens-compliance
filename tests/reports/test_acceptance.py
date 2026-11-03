"""Aceptación de `reports-scheduling` por la CLI: informe trazable, fuente caída e invariantes.

Mundo `ready` del motor de checks (GitHub recogido y en PASS) con conectores falsos que se
ejecutan en subprocesos `spawn`. Reloj de la CLI inyectado. Los escenarios usan un ciclo
construido una vez por módulo y solo leen de él.
"""

import json
from datetime import timedelta

import pytest
import yaml

from ens_evidence.backup.environment import ACCESS_KEY_VAR, PASSWORD_VAR, SECRET_KEY_VAR
from ens_evidence.checks.store import scan_runs
from ens_evidence.evidence.manifests import list_manifests
from ens_evidence.reports.model import DailyReport
from ens_evidence.reports.store import cycle_finished, run_ids, stages_of
from ens_evidence.workspace import LocalStore

from . import acceptance_fakes as fakes
from .acceptance_helpers import (
    LATE,
    METADATA,
    START,
    AppendOnlyStore,
    connector,
    copy_of,
    counts,
    cycle_world,
    daily_html,
    daily_json,
    everything_stored,
    fake_plugins,
    html_rows,
    instants,
    is_aware,
    is_sourced,
    is_unavailable,
    read_only,
    sections_under,
    source_matches_store,
    source_refs,
    stdout_json,
    walk,
    without_sources,
)

HEALTHY = {"github": "ok", "google-workspace": "ok"}
DROPPED = {"github": "raise", "cloudflare": "error", "google-workspace": "ok"}
CYCLE = ("report", "daily", "--cycle")


def other_than(values: dict[str, int], status: str) -> int:
    return sum(n for key, n in values.items() if key != status)


@pytest.fixture(scope="module", params=["sanos", "con_fallos"])
def traced(request, tmp_path_factory):
    modes, extra = (
        (HEALTHY, ("google-workspace",))
        if request.param == "sanos"
        else (DROPPED, ("cloudflare", "google-workspace"))
    )
    with pytest.MonkeyPatch.context() as monkeypatch:
        world = cycle_world(tmp_path_factory.mktemp("traced"), monkeypatch, modes, extra=extra)
        world.cycle = world.run(*CYCLE)
        assert world.cycle.exit_code == (0 if request.param == "sanos" else 3), world.cycle.output
        yield world


@pytest.fixture(scope="module")
def dropped(tmp_path_factory):
    with pytest.MonkeyPatch.context() as monkeypatch:
        world = cycle_world(tmp_path_factory.mktemp("dropped"), monkeypatch, DROPPED)
        baseline = stdout_json(world)
        assert baseline["readiness"]["value"]["readiness"] == "READY"
        before = {m.id for m in list_manifests(world.store).manifests}
        world.cycle = world.run(*CYCLE)
        world.new_manifests = [
            m for m in list_manifests(world.store).manifests if m.id not in before
        ]
        yield world


# --- escenario: informe trazable --------------------------------------------------------------


def test_cada_seccion_lleva_fuentes_o_es_no_disponible_con_su_motivo(traced):
    document = daily_json(traced.store)

    data_keys = set(document) - METADATA
    assert {"assets", "checks", "connectors", "readiness", "expiring", "changes"} <= data_keys
    for key in sorted(data_keys):
        found = sections_under(document[key])
        assert found, f"{key} no es ni Sourced ni Unavailable"
        for path, node in found:
            if is_sourced(node):
                assert node["sources"], f"{key}: {path}"
            else:
                assert node["reason"].strip(), f"{key}: {path}"
    for path, node in walk(document):
        if isinstance(node, dict) and "sources" in node:
            assert node["sources"], path
    kinds = {is_sourced(n) for _, n in sections_under(document)}
    assert kinds == {True, False}


def test_cada_fuente_citada_existe_en_el_store_y_su_sha256_coincide(traced):
    document = daily_json(traced.store)

    refs = source_refs(document)

    assert len(refs) > 20
    assert [r["key"] for _, r in refs if not source_matches_store(traced.store, r)] == []


def test_cada_instante_del_informe_lleva_zona_horaria(traced):
    document = daily_json(traced.store)

    found = instants(document)

    assert len(found) > 20
    assert [path for path, text in found if not is_aware(text)] == []


def test_ninguna_fuente_es_posterior_al_corte_del_informe(traced):
    document = daily_json(traced.store)
    cutoff = document["cycle"]["data_cutoff"]

    late = [r["key"] for _, r in source_refs(document) if r["at"] > cutoff]

    assert late == []


def test_cada_fila_de_dato_del_html_tiene_su_celda_de_fuente(traced):
    html, document = daily_html(traced.store), daily_json(traced.store)
    parsed = html_rows(html)
    text = "".join(parsed.text)

    assert len(parsed.rows) > 40
    assert [row for row in parsed.rows if len(row) != 2 or not all(c.strip() for c in row)] == []
    assert any(row[0] == "Fuentes" and "sha256:" in row[1] for row in parsed.rows)
    for _, ref in source_refs(document):
        assert ref["key"] in text and ref["sha256"] in text
    for _, node in sections_under(document):
        if is_unavailable(node):
            assert node["reason"] in text


# --- escenario: fuente caída ------------------------------------------------------------------


def test_el_informe_muestra_error_en_la_recogida_y_en_los_checks_de_los_dos_conectores(dropped):
    document, parsed = daily_json(dropped.store), html_rows(daily_html(dropped.store))

    for name in ("github", "cloudflare"):
        entry = connector(document, name)
        assert entry["collection"]["value"] == "ERROR", name
        assert counts(entry["checks"])["ERROR"] > 0, name
        assert other_than(counts(entry["checks"]), "ERROR") == 0, name
    states = {
        title: row[1] for title, row in parsed.titled if row[0] == "estado" and "recogida" in title
    }
    assert states["Conector github: recogida"] == "ERROR · fase: recogida (excepción RuntimeError)"
    assert states["Conector cloudflare: recogida"] == "ERROR"
    assert states["Conector google-workspace: recogida"] == "recogida correcta"


def test_el_resto_de_conectores_tiene_evidencia_nueva_y_una_ejecucion_en_el_mismo_ciclo(dropped):
    cycle = daily_json(dropped.store)["cycle"]
    runs = scan_runs(dropped.store).runs

    assert {m.source for m in dropped.new_manifests} == {"github", "cloudflare", "google-workspace"}
    attempts = {a["connector"]: a for a in cycle["connector_attempts"]}
    assert {n: a["last_status"] for n, a in attempts.items()} == {
        "github": "ERROR",
        "cloudflare": "ERROR",
        "google-workspace": "SUCCESS",
    }
    for name in attempts:
        last = runs[name][-1].run
        assert last.complete, name
        assert cycle["started_at"] <= last.started_at.isoformat().replace("+00:00", "Z")
        assert last.finished_at.isoformat().replace("+00:00", "Z") <= cycle["data_cutoff"]
    workspace = connector(daily_json(dropped.store), "google-workspace")
    assert counts(workspace["checks"])["PASS"] > 0


def test_la_recogida_caida_deja_un_registro_veraz_y_no_datos_inventados(dropped):
    artifacts = {
        m.source: json.loads(dropped.store.read_bytes(m.artifact.key))
        for m in dropped.new_manifests
    }

    assert artifacts["github"]["data"] == {
        "error": "collect_crashed:RuntimeError",
        "generated_by": "scheduler",
        "phase": "collect",
    }
    assert artifacts["cloudflare"]["data"] == {"error": "api_unavailable"}
    assert artifacts["cloudflare"]["collection_status"] == "ERROR"


def test_la_preparacion_es_not_ready_y_la_salida_es_3(dropped):
    document = daily_json(dropped.store)

    assert dropped.cycle.exit_code == 3
    assert document["readiness"]["value"]["readiness"] == "NOT_READY"
    assert any(
        "github.secret_scanning_enabled" in b for b in document["readiness"]["value"]["blockers"]
    )


# --- invariante: nunca PASS por ausencia -------------------------------------------------------


def test_un_conector_configurado_sin_evidencia_da_unknown(tmp_path, monkeypatch):
    world = cycle_world(tmp_path, monkeypatch, HEALTHY, extra=("cloudflare",))
    checked = world.run("check", "--all")
    assert checked.exit_code == 3, checked.output  # runbook del motor: UNKNOWN sin FAIL -> 3

    document = stdout_json(world)

    entry = connector(document, "cloudflare")
    assert counts(entry["checks"])["UNKNOWN"] > 0
    assert other_than(counts(entry["checks"]), "UNKNOWN") == 0
    assert (
        counts(document["checks"])["PASS"]
        == counts(connector(document, "github")["checks"])["PASS"]
    )


def test_un_conector_no_configurado_no_suma(tmp_path, monkeypatch):
    world = cycle_world(tmp_path, monkeypatch, HEALTHY, extra=())

    document = stdout_json(world)

    github = counts(connector(document, "github")["checks"])
    assert github["PASS"] > 0
    assert counts(document["checks"]) == github
    for name in ("cloudflare", "endpoint", "google-workspace", "restic"):
        entry = connector(document, name)
        assert is_unavailable(entry["collection"]) and is_unavailable(entry["checks"]), name
        assert "configuración ausente" in entry["checks"]["reason"], name


@pytest.mark.parametrize("mode", ["raise", "error", "leak"])
def test_una_recogida_caida_no_recupera_el_pass_anterior(tmp_path, monkeypatch, mode):
    world = cycle_world(tmp_path, monkeypatch, {"github": mode}, extra=())
    before = counts(connector(stdout_json(world), "github")["checks"])
    assert before["PASS"] > 0

    cycle = world.run(*CYCLE)

    assert cycle.exit_code == 3, cycle.output
    document = daily_json(world.store)
    after = counts(connector(document, "github")["checks"])
    assert after["PASS"] == 0 and after["ERROR"] > 0
    assert counts(document["checks"])["PASS"] == 0
    assert document["readiness"]["value"]["readiness"] == "NOT_READY"


def test_un_rechazo_de_prepare_en_la_recogida_manual_no_deja_visible_el_pass_anterior(
    tmp_path, monkeypatch
):
    world = cycle_world(tmp_path, monkeypatch, {"github": "ok"}, extra=())
    before = counts(connector(stdout_json(world), "github")["checks"])
    assert before["PASS"] > 0
    monkeypatch.setenv(fakes.MODES_ENV, json.dumps({"github": "reject"}))
    monkeypatch.setattr("ens_evidence.cli.connector_registry", lambda: fake_plugins().registry)

    collected = world.run("collect", "github")

    assert collected.exit_code == 2, collected.output
    assert fakes.REJECTION in collected.output
    assert world.run("check", "--all").exit_code in (0, 1, 3)
    after = counts(connector(stdout_json(world), "github")["checks"])
    assert after["PASS"] == 0 and after["ERROR"] > 0
    assert fakes.REJECTION not in everything_stored(world.store)


# --- invariante: fuente única ------------------------------------------------------------------


def test_la_preparacion_del_informe_es_la_de_assessment_status_en_el_mismo_instante(
    traced, monkeypatch
):
    read_only(monkeypatch)
    report = daily_json(traced.store)
    cutoff = report["cycle"]["data_cutoff"]

    status = json.loads(
        traced.run("assessment", "status", "--at", cutoff, "--format", "json").output
    )

    value = report["readiness"]["value"]
    assert value["readiness"] == status["readiness"]
    text = traced.run("assessment", "status", "--at", cutoff).output
    shown = [line.strip() for line in text.splitlines() if line.startswith("  ")]
    assert value["blockers"] == shown[: len(value["blockers"])]


# --- invariante: determinismo ------------------------------------------------------------------


def test_dos_builds_sobre_una_copia_del_workspace_dan_los_mismos_bytes(
    dropped, tmp_path, monkeypatch
):
    read_only(monkeypatch)
    copy = copy_of(dropped, tmp_path / "copy")
    at = (LATE + timedelta(hours=1)).isoformat()

    for fmt in ("json", "html"):
        args = ("report", "daily", "--stdout", "--format", fmt, "--at", at)
        original, copied = dropped.run(*args), copy.run(*args)
        assert original.exit_code == copied.exit_code == 3
        assert original.output == copied.output
        assert dropped.run(*args).output == original.output


# --- invariante: solo adición ------------------------------------------------------------------


def test_el_guardian_de_solo_adicion_falla_ante_una_reescritura(tmp_path):
    store = AppendOnlyStore(LocalStore(tmp_path))
    store.write_text("evidence/a.txt", "uno")
    with pytest.raises(AssertionError):
        store.write_text("evidence/a.txt", "dos")
    with pytest.raises(AssertionError):
        store.write_bytes("evidence/a.txt", b"dos")
    store.write_text("otro/a.txt", "uno")
    store.write_text("otro/a.txt", "dos")
    assert store.violations == ["write_text evidence/a.txt", "write_bytes evidence/a.txt"]


def test_el_ciclo_completo_solo_anade_bajo_evidence_y_aun_asi_pasa(tmp_path, monkeypatch):
    world = cycle_world(tmp_path, monkeypatch, HEALTHY, extra=("google-workspace",))
    guarded = AppendOnlyStore(world.store)
    monkeypatch.setattr("ens_evidence.cli.open_store", lambda settings: guarded)

    first, second = world.run(*CYCLE), world.run(*CYCLE)

    assert (first.exit_code, second.exit_code) == (0, 0), (first.output, second.output)
    assert guarded.violations == []
    assert len(guarded.new_keys) > 20
    assert len(run_ids(world.store)) == 2
    assert all(cycle_finished(world.store, run_id) for run_id in run_ids(world.store))


# --- invariante: sin red -----------------------------------------------------------------------


@pytest.fixture
def world_before_offline(tmp_path, monkeypatch):
    """El mundo se construye antes de activar `offline` (construirlo usa entry points)."""
    return cycle_world(tmp_path, monkeypatch, DROPPED)


def test_report_daily_sin_cycle_no_usa_red_ni_procesos_ni_plugins(
    world_before_offline,
    offline,  # noqa: F811
    monkeypatch,
):
    world = world_before_offline

    def forbidden(*_args, **_kwargs):
        raise AssertionError("report daily sin --cycle no puede usar esto")

    for path in (
        "ens_evidence.cli.load_plugins",
        "ens_evidence.scheduling.collect.collect_all",
    ):
        monkeypatch.setattr(path, forbidden)

    written, printed = world.run("report", "daily"), world.run("report", "daily", "--stdout")

    assert written.exit_code == printed.exit_code == 3, (written.output, printed.output)
    assert DailyReport.model_validate(json.loads(printed.output)).cycle is None
    assert run_ids(world.store) == []


# --- invariante: ciclo interrumpido ------------------------------------------------------------


class Crash(BaseException):
    """Una caída del proceso: no la captura ningún `except Exception`."""


def crash_in_checks(monkeypatch) -> None:
    def crash(*_args, **_kwargs):
        raise Crash

    monkeypatch.setattr("ens_evidence.scheduling.cycle.run_all", crash)


def test_un_ciclo_interrumpido_aparece_en_el_siguiente_informe(tmp_path, monkeypatch):
    world = cycle_world(tmp_path, monkeypatch, HEALTHY, extra=("google-workspace",))
    with monkeypatch.context() as crashing:
        crash_in_checks(crashing)
        with pytest.raises(Crash):
            world.run(*CYCLE)
    (interrupted,) = run_ids(world.store)
    assert [m.stage for m in stages_of(world.store, interrupted).stages] == ["started", "collected"]

    document = stdout_json(world)

    listed = document["interrupted_cycles"]["value"]
    assert [(c["run_id"], c["last_stage"]) for c in listed] == [(interrupted, "collected")]
    assert document["interrupted_cycles"]["sources"]


def test_el_ciclo_del_dia_siguiente_lista_el_interrumpido_sin_reanudarlo(tmp_path, monkeypatch):
    world = cycle_world(tmp_path, monkeypatch, HEALTHY, extra=("google-workspace",))
    with monkeypatch.context() as crashing:
        crash_in_checks(crashing)
        with pytest.raises(Crash):
            world.run(*CYCLE)
    (interrupted,) = run_ids(world.store)
    world.tick.current = START + timedelta(days=1)

    result = world.run(*CYCLE)

    assert result.exit_code == 0, result.output
    document = daily_json(world.store, "2026-10-31")
    assert document["cycle"]["run_id"] != interrupted
    assert [c["run_id"] for c in document["interrupted_cycles"]["value"]] == [interrupted]
    assert not cycle_finished(world.store, interrupted)


# --- invariante: copia activada por defecto sin credenciales ----------------------------------


def stage_codes(store) -> list[tuple[str, str | None]]:
    (run_id,) = run_ids(store)
    return [(m.stage, m.backup_code) for m in stages_of(store, run_id).stages]


def test_la_copia_activada_por_defecto_sin_credenciales_termina_con_backup_failed(
    tmp_path, monkeypatch
):
    world = cycle_world(tmp_path, monkeypatch, HEALTHY, extra=("google-workspace",))

    result = world.run(*CYCLE)

    assert result.exit_code == 0, result.output
    assert "La copia ha fallado" in result.output
    stages = stage_codes(world.store)
    assert [s for s, _ in stages] == [
        "started",
        "collected",
        "checked",
        "reported",
        "backup_failed",
    ]
    assert dict(stages)["backup_failed"] == "not_configured"
    (run_id,) = run_ids(world.store)
    assert cycle_finished(world.store, run_id)


def test_el_informe_siguiente_muestra_la_copia_fallida(tmp_path, monkeypatch):
    world = cycle_world(tmp_path, monkeypatch, HEALTHY, extra=("google-workspace",))
    world.run(*CYCLE)
    world.tick.current = START + timedelta(days=1)

    world.run(*CYCLE)

    stated = json.dumps(without_sources(daily_json(world.store, "2026-10-31")), ensure_ascii=False)
    assert any(
        word in stated.lower() for word in ("backup_failed", "copia fallida", "not_configured")
    )
    backup = daily_json(world.store, "2026-10-31")["backup"]
    assert backup["value"]["outcome"] == "backup_failed"
    assert backup["value"]["code"] == "not_configured"
    assert backup["value"]["configuration_error"] is True
    assert backup["sources"][0]["kind"] == "cycle"
    assert backup["sources"][0]["key"].endswith(".backup_failed.json")
    html = daily_html(world.store, "2026-10-31")
    assert "copia fallida: sin ajustes de copia" in html
    assert "fallo de configuración" in html


def test_la_copia_fallida_con_ajustes_sin_credenciales_queda_en_el_informe_mensual(
    tmp_path, monkeypatch
):
    world = cycle_world(tmp_path, monkeypatch, HEALTHY, extra=("google-workspace",))
    settings = yaml.safe_load(world.settings.read_text())
    settings["backup"] = {"restic_repository": str(tmp_path / "repo")}
    world.settings.write_text(yaml.safe_dump(settings))
    for variable in (PASSWORD_VAR, ACCESS_KEY_VAR, SECRET_KEY_VAR):
        monkeypatch.delenv(variable, raising=False)

    cycle = world.run(*CYCLE)

    assert cycle.exit_code == 0, cycle.output
    assert dict(stage_codes(world.store))["backup_failed"] == "invalid_credentials"
    monthly = json.loads(
        world.run("report", "monthly", "--stdout", "--at", "2026-11-01T08:00:00+00:00").output
    )
    assert monthly["backups"]["value"]["counts"]["copia fallida"] == 1
    assert monthly["backups"]["sources"][0]["kind"] == "backup_run"
