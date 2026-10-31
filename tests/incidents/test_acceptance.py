"""Los dos escenarios de `docs/specs/incident-register.md`, por la CLI y de punta a punta.

Workspace real: alcance aprobado, Declaración BÁSICA vigente con op.exp.7 y op.exp.9, respuestas
completas, `docs build` y los seis documentos aprobados. El flujo de incidentes es el de producción.
"""

import json

import pytest
from matrix.acceptance_helpers import import_manual_evidence, line_of, link, review
from matrix.evaluate_helpers import NOW

from ens_evidence.evidence.manifests import list_manifests

from .acceptance_helpers import (
    NEW,
    PROCEDURE,
    PROCEDURE_MEASURE,
    REGISTER_MEASURE,
    SELF,
    Scenario,
    blocker_details,
    build_scenario,
    export,
    fix_owner_and_periodicity,
    row,
    run_simulation,
)

SIMULATION = "SIM-2026-001"
CLAUSES_OF_9 = {"op.exp.9.1", "op.exp.9.2", "op.exp.9.3"}


@pytest.fixture
def scenario(tmp_path, monkeypatch) -> Scenario:
    return build_scenario(tmp_path, monkeypatch)


def link_simulation(scenario: Scenario, history: str) -> None:
    """Enlaza como sugiere `incident close`, con responsable y periodicidad ya fijados."""
    run = scenario.run
    fix_owner_and_periodicity(run)
    scenario.at(0)
    link(run, REGISTER_MEASURE, "evidence", history, "base")
    link(run, PROCEDURE_MEASURE, "document", PROCEDURE, "op.exp.7.1,op.exp.7.2")


def review_both(scenario: Scenario) -> None:
    scenario.clock.now = NOW
    for measure in (PROCEDURE_MEASURE, REGISTER_MEASURE):
        reviewed = review(scenario.run, measure)
        assert reviewed.exit_code == 0, reviewed.output


# --- escenario 1: incidente simulado ----------------------------------------------------------


def test_el_simulacro_deja_tres_registros_y_cada_uno_con_su_evidencia_verificada(scenario):
    history = run_simulation(scenario)

    verified = scenario.run("incident", "verify")
    shown = json.loads(scenario.run("incident", "show", SIMULATION, "--json").output)
    manifests = {m.artifact.sha256: m for m in list_manifests(scenario.store).manifests}

    assert verified.exit_code == 0, verified.output
    assert [r["kind"] for r in shown["records"]] == ["opened", "updated", "closed"]
    assert shown["simulated"] is True and shown["problems"] == []
    for record in shown["records"]:
        assert manifests[record["sha256"]].source == "incident-record"
        assert scenario.run("evidence", "verify", manifests[record["sha256"]].id).exit_code == 0
    assert scenario.run("evidence", "verify", history).exit_code == 0
    assert scenario.run("evidence", "verify", "--all").exit_code == 0
    assert len(manifests) == 4  # tres registros y el historial


def test_el_historial_recoge_la_actuacion_de_emergencia_y_el_procedimiento_aprobado(scenario):
    run_simulation(scenario)

    records = json.loads(scenario.run("incident", "show", SIMULATION, "--json").output)["records"]
    last = records[-1]["state"]

    assert [a["kind"] for a in last["actions"]] == ["emergency"]
    assert last["procedure"]["name"] == PROCEDURE
    assert last["closure"]["auditable_events_review"]["result"] == "no_change"
    assert last["closure"]["legal_relevance"] == "no"


def test_el_historial_se_enlaza_a_op_exp_9_y_el_procedimiento_a_op_exp_7_con_responsable(scenario):
    history = run_simulation(scenario)

    link_simulation(scenario, history)
    document = export(scenario.run)

    for measure in (REGISTER_MEASURE, PROCEDURE_MEASURE):
        assert row(document, measure)["owner"] == {"name": "Ana", "role": "system_administrator"}
        assert row(document, measure)["review_every_days"] == 365
    assert {(m["kind"], m["target"]) for m in row(document, REGISTER_MEASURE)["manual"]} == {
        ("evidence", history)
    }
    assert {(m["kind"], m["target"]) for m in row(document, PROCEDURE_MEASURE)["manual"]} == {
        ("org_document", PROCEDURE)
    }


def test_op_exp_9_queda_sin_huecos_de_clausula_y_solo_con_revision_pendiente(scenario):
    history = run_simulation(scenario)
    link_simulation(scenario, history)

    status = scenario.run("matrix", "status", REGISTER_MEASURE)
    exported = row(export(scenario.run), REGISTER_MEASURE)

    assert status.exit_code == 1
    assert "faltan evidencias" not in status.output
    # «sin revisar» se muestra como MISSING_REVIEW y su eje de evaluación, NOT_ASSESSED.
    assert blocker_details(exported) == {
        ("MISSING_REVIEW", "review_missing"),
        ("NOT_COMPLIANT", "NOT_ASSESSED"),
    }
    assert exported["manual"][0]["counts"] is True
    assert exported["implemented"] is False and exported["last_review"] is None


def test_tras_la_revision_op_exp_7_y_op_exp_9_quedan_sin_bloqueos_y_el_export_dice_simulacro(
    scenario,
):
    history = run_simulation(scenario)
    link_simulation(scenario, history)

    review_both(scenario)
    document = export(scenario.run)
    markdown = scenario.run("matrix", "export", "--stdout", "--format", "md")

    for measure in (REGISTER_MEASURE, PROCEDURE_MEASURE):
        assert scenario.run("matrix", "status", measure).exit_code == 0
        assert row(document, measure)["implemented"] is True
        assert row(document, measure)["blockers"] == []
    assert row(document, REGISTER_MEASURE)["manual"][0]["simulated"] is True
    assert row(document, PROCEDURE_MEASURE)["manual"][0].get("simulated", False) is False
    assert "(simulacro)" in line_of(
        scenario.run("matrix", "status", REGISTER_MEASURE).output, REGISTER_MEASURE
    )
    assert "simulacro" in markdown.output


def test_sin_procedimiento_aprobado_el_simulacro_se_rechaza_y_no_deja_rastro(tmp_path, monkeypatch):
    scenario = build_scenario(tmp_path, monkeypatch, approve_procedure=False)
    before = scenario.snapshot()

    rejected = scenario.run(*NEW, "--simulated", *SELF)

    assert rejected.exit_code == 1
    assert PROCEDURE in rejected.output
    assert scenario.snapshot() == before
    assert scenario.store.list("incidents") == []


def tamper(scenario: Scenario, seq: int) -> None:
    key = next(k for k in scenario.store.list(f"incidents/{SIMULATION}") if f"/{seq:06d}-" in k)
    text = scenario.store.read_text(key)
    scenario.store.write_text(key, text.replace("Acceso indebido", "Acceso permitido"))


def assert_blocked_everywhere(scenario: Scenario, history: str) -> None:
    status = scenario.run("matrix", "status", REGISTER_MEASURE)
    exported = row(export(scenario.run), REGISTER_MEASURE)

    assert status.exit_code == 1
    assert f"incident_history_broken:{history}" in {d for _, d in blocker_details(exported)}
    assert export(scenario.run)["readiness"] == "NOT_READY"


def test_un_registro_editado_a_mano_da_historial_roto_y_bloquea_estado_export_y_salida(scenario):
    history = run_simulation(scenario)
    link_simulation(scenario, history)
    review_both(scenario)
    assert scenario.run("matrix", "status", REGISTER_MEASURE).exit_code == 0

    tamper(scenario, seq=1)

    verified = scenario.run("incident", "verify")
    assert verified.exit_code == 1
    assert_blocked_everywhere(scenario, history)


def test_si_se_borra_el_ultimo_registro_verify_lo_informa_y_la_matriz_bloquea(scenario):
    history = run_simulation(scenario)
    link_simulation(scenario, history)
    review_both(scenario)
    last = next(k for k in scenario.store.list(f"incidents/{SIMULATION}") if "/000003-" in k)
    (scenario.path / last).unlink()

    verified = scenario.run("incident", "verify")

    assert verified.exit_code == 1
    assert SIMULATION in verified.output
    assert_blocked_everywhere(scenario, history)


# --- escenario 2: registro vacío --------------------------------------------------------------


def test_sin_incidentes_op_exp_9_aparece_sin_evidencia_y_bloquea(scenario):
    fix_owner_and_periodicity(scenario.run)
    scenario.at(0)

    status = scenario.run("matrix", "status", REGISTER_MEASURE)
    document = export(scenario.run)

    assert status.exit_code == 1
    assert {("MISSING_EVIDENCE", f"clause:{c}") for c in CLAUSES_OF_9} <= blocker_details(
        row(document, REGISTER_MEASURE)
    )
    assert document["readiness"] == "NOT_READY"
    assert scenario.run("matrix", "status").exit_code == 1


def test_vincular_el_procedimiento_a_op_exp_9_no_levanta_el_bloqueo(scenario):
    fix_owner_and_periodicity(scenario.run)
    scenario.at(0)

    linked = scenario.run(
        *("matrix", "link", REGISTER_MEASURE, "--document", PROCEDURE, "--covers", "base")
    )
    status = scenario.run("matrix", "status", REGISTER_MEASURE)

    assert linked.exit_code == 1
    assert "op.exp.9 solo se sustenta con el historial de un incidente cerrado" in linked.output
    assert status.exit_code == 1
    assert "faltan evidencias" in status.output


def test_una_evidencia_manual_generica_no_se_puede_vincular_a_op_exp_9(scenario, tmp_path):
    fix_owner_and_periodicity(scenario.run)
    scenario.at(0)
    manual = import_manual_evidence(scenario.run, tmp_path)

    linked = scenario.run(
        *("matrix", "link", REGISTER_MEASURE, "--evidence", manual, "--covers", "base")
    )

    assert linked.exit_code == 1
    assert "op.exp.9 solo se sustenta con el historial de un incidente cerrado" in linked.output
    assert row(export(scenario.run), REGISTER_MEASURE)["manual"] == []
