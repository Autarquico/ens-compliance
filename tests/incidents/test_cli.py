import json
import re
from datetime import timedelta

import pytest
import yaml
from typer.testing import CliRunner

from ens_evidence.cli import app
from ens_evidence.evidence.manifests import list_manifests
from ens_evidence.incidents import IncidentRejected
from ens_evidence.matrix import MATRIX_KEY

from .helpers import DETECTED, RECORDED
from .world import World

DETECTED_AT = DETECTED.isoformat()
NEW = ["incident", "new", "--title", "Acceso indebido", "--detected-at", DETECTED_AT, "--by", "Ana"]
CLOSE = [
    "--by",
    "Ana",
    "--conclusion",
    "Contenido el acceso y rotadas las credenciales de servicio.",
    "--auditable-events-result",
    "no_change",
    "--auditable-events-detail",
    "Los eventos ya cubrian el caso.",
    "--legal-relevance",
    "no",
]


@pytest.fixture
def cli(world: World, tmp_path, monkeypatch):
    """CLI sobre el workspace de `world`; el reloj marca dos horas tras la detección."""
    now = [RECORDED]
    monkeypatch.setattr("ens_evidence.cli.clock", lambda: now[0])
    settings = tmp_path / "settings.yaml"
    settings.write_text(
        yaml.safe_dump({"workspace": {"backend": "local", "local": {"path": str(world.root)}}})
    )
    runner = CliRunner()

    def run(*args: str, hours: int = 0):
        now[0] = RECORDED + timedelta(hours=hours)
        return runner.invoke(app, ["--settings", str(settings), *args])

    return run


def stored(world: World) -> list[str]:
    return world.keys("incidents")


def evidence_count(world: World) -> int:
    return len(list_manifests(world.store).manifests)


def open_incident(cli, *extra: str):
    result = cli(*NEW, *extra)
    assert result.exit_code == 0, result.output
    return result


def close_incident(cli, incident="INC-2026-001", *extra: str):
    result = cli("incident", "close", incident, *CLOSE, *extra, hours=3)
    assert result.exit_code == 0, result.output
    return result


class TestNew:
    def test_prints_key_evidence_sha_and_declared_name_notice(self, cli, world):
        world.declare_levels()
        result = open_incident(cli, "--severity", "ALTO", "--asset", "APP-001")
        assert "INC-2026-001" in result.output
        assert re.search(r"incidents/INC-2026-001/000001-\S+\.yaml", result.output)
        assert re.search(r"EV-\d{8}-[0-9a-f]{8}", result.output)
        assert re.search(r"sha256: [0-9a-f]{64}", result.output)
        assert "El nombre es declarado; no se autentica" in result.output
        assert world.last_state()["severity"] == "alto"

    def test_second_incident_takes_the_next_number(self, cli):
        open_incident(cli)
        assert "INC-2026-002" in open_incident(cli).output

    def test_privacy_rejection_writes_nothing_and_names_the_field_not_the_value(self, cli, world):
        result = cli(*NEW, "--summary", "Lo reporto lucia.perez@ejemplo.org por correo")
        assert result.exit_code == 1
        assert "analysis.summary" in result.output
        assert "lucia.perez" not in result.output
        assert stored(world) == []
        assert evidence_count(world) == 0

    def test_ip_is_accepted_with_a_warning(self, cli, world):
        result = open_incident(cli, "--summary", "Origen 203.0.113.7 contra el portal")
        assert "dirección IP" in result.output
        assert stored(world)

    def test_secret_in_free_text_is_rejected(self, cli, world):
        result = cli(*NEW, "--summary", "AKIAABCDEFGHIJKLMNOP")
        assert result.exit_code == 1
        assert "AKIAABCDEFGHIJKLMNOP" not in result.output
        assert stored(world) == []

    def test_simulation_without_approved_procedure_is_rejected(self, cli, world):
        result = cli(*NEW, "--simulated")
        assert result.exit_code == 1
        assert "procedimiento-incidentes" in result.output
        assert stored(world) == []
        assert evidence_count(world) == 0

    def test_simulation_with_approved_procedure_is_labelled(self, cli, world, monkeypatch):
        world.approve_procedure(monkeypatch)
        result = open_incident(cli, "--simulated")
        assert "SIM-2026-001 (SIMULACRO)" in result.output
        assert any("SIM-2026-001" in key for key in stored(world))

    def test_real_incident_without_procedure_is_accepted_with_a_warning(self, cli):
        result = open_incident(cli)
        assert "Aviso" in result.output
        assert "sin procedimiento" in result.output

    def test_severity_must_be_a_declared_level(self, cli, world):
        world.declare_levels()
        result = cli(*NEW, "--severity", "catastrofico")
        assert result.exit_code == 1
        assert "severity" in result.output
        assert stored(world) == []

    def test_alert_id_needs_a_source_system(self, cli, world):
        result = cli(*NEW, "--alert-id", "a-1")
        assert result.exit_code == 2
        assert "--source-system" in result.output
        assert stored(world) == []

    def test_detected_at_needs_a_timezone(self, cli):
        args = [*NEW]
        args[args.index(DETECTED_AT)] = "2026-10-15T12:00:00"
        result = cli(*args)
        assert result.exit_code == 2
        assert "--detected-at" in result.output

    def test_blank_declared_name_is_rejected(self, cli, world):
        args = [*NEW]
        args[-1] = " "
        result = cli(*args)
        assert result.exit_code == 1
        assert "--by" in result.output
        assert stored(world) == []


class TestUpdate:
    def test_adds_a_report_and_keeps_what_was_there(self, cli, world):
        open_incident(cli, "--asset", "APP-001")
        result = cli(
            "incident",
            "update",
            "INC-2026-001",
            "--by",
            "Ana",
            "--summary",
            "Credenciales reutilizadas",
            "--asset",
            "APP-002",
            "--personal-data",
            "no",
            "--action",
            "Credenciales rotadas",
            "--action-kind",
            "emergency",
            hours=1,
        )
        assert result.exit_code == 0, result.output
        assert "registro 2 (updated)" in result.output
        assert "El nombre es declarado; no se autentica" in result.output
        state = world.last_state()
        assert state["assets"] == ["APP-001", "APP-002"]
        assert state["personal_data"] == "no"
        assert state["actions"][0]["kind"] == "emergency"

    def test_links_evidence(self, cli, world):
        open_incident(cli)
        evidence = world.evidence()
        result = cli(
            "incident", "update", "INC-2026-001", "--by", "Ana", "--evidence", evidence, hours=1
        )
        assert result.exit_code == 0, result.output
        assert world.last_state()["evidence_ids"] == [evidence]

    def test_without_changes_is_rejected(self, cli, world):
        open_incident(cli, "--asset", "APP-001")
        before = stored(world)
        result = cli("incident", "update", "INC-2026-001", "--by", "Ana", hours=1)
        assert result.exit_code == 1
        assert "no cambia nada" in result.output
        repeated = cli("incident", "update", "INC-2026-001", "--by", "Ana", "--asset", "APP-001")
        assert repeated.exit_code == 1
        assert stored(world) == before

    def test_unknown_incident_is_rejected(self, cli):
        result = cli("incident", "update", "INC-2026-009", "--by", "Ana", "--summary", "x")
        assert result.exit_code == 1
        assert "no existe" in result.output

    def test_malformed_id_is_a_usage_error(self, cli, world):
        result = cli("incident", "update", "../x", "--by", "Ana", "--summary", "x")
        assert result.exit_code == 2
        assert stored(world) == []

    def test_privacy_rejection_writes_nothing(self, cli, world):
        open_incident(cli)
        before, count = stored(world), evidence_count(world)
        result = cli(
            "incident",
            "update",
            "INC-2026-001",
            "--by",
            "Ana",
            "--summary",
            "Avisar al 612 345 678",
            hours=1,
        )
        assert result.exit_code == 1
        assert "612 345" not in result.output
        assert stored(world) == before
        assert evidence_count(world) == count

    def test_action_options_need_an_action(self, cli):
        open_incident(cli)
        result = cli(
            "incident", "update", "INC-2026-001", "--by", "Ana", "--action-kind", "emergency"
        )
        assert result.exit_code == 2
        assert "--action" in result.output

    def test_closed_incident_takes_no_more_updates(self, cli):
        open_incident(cli, "--personal-data", "no")
        close_incident(cli)
        result = cli(
            "incident", "update", "INC-2026-001", "--by", "Ana", "--summary", "tarde", hours=4
        )
        assert result.exit_code == 1
        assert "cerrado" in result.output


class TestClose:
    def test_prints_history_and_suggests_the_matrix_link_without_running_it(self, cli, world):
        open_incident(cli, "--personal-data", "no")
        result = close_incident(cli)
        history = re.search(r"Historial: (EV-\d{8}-[0-9a-f]{8})", result.output)
        assert history
        assert f"matrix link op.exp.9 --evidence {history.group(1)} --covers base" in result.output
        assert "no se han ejecutado" in result.output
        assert "El nombre es declarado; no se autentica" in result.output
        assert not world.store.exists(MATRIX_KEY)
        assert world.last_state()["status"] == "closed"

    def test_incomplete_closure_is_rejected_and_nothing_is_written(self, cli, world):
        open_incident(cli, "--personal-data", "no")
        before, count = stored(world), evidence_count(world)
        result = cli(
            "incident", "close", "INC-2026-001", *CLOSE[:-1], "yes", hours=3
        )  # relevancia YES sin asesoramiento ni evidencias preservadas
        assert result.exit_code == 1
        assert "legal_advice" in result.output or "asesoramiento" in result.output
        assert stored(world) == before
        assert evidence_count(world) == count
        assert world.last_state()["status"] == "open"

    def test_personal_data_unknown_blocks_the_closure(self, cli, world):
        open_incident(cli)
        result = cli("incident", "close", "INC-2026-001", *CLOSE, hours=3)
        assert result.exit_code == 1
        assert "personal_data" in result.output

    def test_missing_required_option_is_a_usage_error(self, cli):
        open_incident(cli, "--personal-data", "no")
        result = cli("incident", "close", "INC-2026-001", "--by", "Ana")
        assert result.exit_code == 2

    def test_legal_relevance_with_advice_and_preserved_evidence(self, cli, world):
        open_incident(cli, "--personal-data", "no")
        evidence = world.evidence()
        result = cli(
            "incident",
            "close",
            "INC-2026-001",
            *CLOSE[:-1],
            "yes",
            "--legal-advice",
            "Despacho Ejemplo Abogados",
            "--preserved-evidence",
            evidence,
            hours=3,
        )
        assert result.exit_code == 0, result.output
        assert world.last_state()["closure"]["preserved_evidence_ids"] == [evidence]

    def test_closing_twice_is_rejected(self, cli):
        open_incident(cli, "--personal-data", "no")
        close_incident(cli)
        result = cli("incident", "close", "INC-2026-001", *CLOSE, hours=4)
        assert result.exit_code == 1
        assert "cerrado" in result.output

    def test_simulation_close_notes_the_label(self, cli, world, monkeypatch):
        world.approve_procedure(monkeypatch)
        open_incident(cli, "--simulated", "--personal-data", "no")
        result = close_incident(cli, "SIM-2026-001")
        assert "simulacro" in result.output


class TestListAndShow:
    def test_list_when_empty(self, cli):
        result = cli("incident", "list")
        assert result.exit_code == 0
        assert "No hay incidentes" in result.output

    def test_list_marks_simulations(self, cli, world, monkeypatch):
        world.approve_procedure(monkeypatch)
        open_incident(cli)
        open_incident(cli, "--simulated")
        lines = cli("incident", "list").output.splitlines()
        real = next(line for line in lines if line.startswith("INC-2026-001"))
        simulated = next(line for line in lines if line.startswith("SIM-2026-001"))
        assert "SIMULACRO" not in real
        assert "[SIMULACRO]" in simulated

    def test_show_prints_the_record_history(self, cli):
        open_incident(cli, "--asset", "APP-001")
        cli("incident", "update", "INC-2026-001", "--by", "Ana", "--summary", "Analisis", hours=1)
        output = cli("incident", "show", "INC-2026-001").output
        assert "Acceso indebido" in output
        assert "APP-001" in output
        assert "000001-" in output and "000002-" in output

    def test_show_marks_a_simulation(self, cli, world, monkeypatch):
        world.approve_procedure(monkeypatch)
        open_incident(cli, "--simulated")
        assert "SIMULACRO" in cli("incident", "show", "SIM-2026-001").output

    def test_show_json(self, cli):
        open_incident(cli)
        result = cli("incident", "show", "INC-2026-001", "--json")
        document = json.loads(result.output)
        assert document["incident_id"] == "INC-2026-001"
        assert document["simulated"] is False
        assert document["problems"] == []
        assert document["records"][0]["seq"] == 1
        assert len(document["records"][0]["sha256"]) == 64

    def test_show_unknown_incident(self, cli):
        assert cli("incident", "show", "INC-2026-009").exit_code == 1


class TestVerify:
    def test_intact_incidents_exit_zero(self, cli):
        open_incident(cli, "--personal-data", "no")
        close_incident(cli)
        assert cli("incident", "verify").exit_code == 0
        assert cli("incident", "verify", "INC-2026-001").exit_code == 0

    def test_nothing_to_verify_exits_zero(self, cli):
        assert cli("incident", "verify").exit_code == 0

    def test_broken_record_exits_one(self, cli, world):
        open_incident(cli)
        cli("incident", "update", "INC-2026-001", "--by", "Ana", "--summary", "Analisis", hours=1)
        world.remove(sorted(k for k in stored(world) if k.endswith(".yaml"))[-1])
        for args in (("incident", "verify"), ("incident", "verify", "INC-2026-001")):
            result = cli(*args)
            assert result.exit_code == 1
            assert "INC-2026-001" in result.output
        assert "ROTO" in cli("incident", "list").output

    def test_broken_incident_takes_no_more_records(self, cli, world):
        open_incident(cli)
        cli("incident", "update", "INC-2026-001", "--by", "Ana", "--summary", "Analisis", hours=1)
        world.remove(sorted(k for k in stored(world) if k.endswith(".yaml"))[-1])
        result = cli("incident", "update", "INC-2026-001", "--by", "Ana", "--summary", "Otro")
        assert result.exit_code == 1
        assert "roto" in result.output

    def test_unknown_incident_exits_one(self, cli):
        assert cli("incident", "verify", "INC-2026-009").exit_code == 1


def test_schemas_are_registered(tmp_path):
    result = CliRunner().invoke(app, ["schema", "export", "--dir", str(tmp_path)])
    assert result.exit_code == 0
    for name in ("incident-record", "incident-history"):
        assert json.loads((tmp_path / f"{name}.schema.json").read_text())["type"] == "object"


class TestHistoryRecovery:
    def cut_closure(self, cli, monkeypatch):
        def failing(*args, **kwargs):
            raise IncidentRejected(["el almacén no responde"])

        open_incident(cli, "--personal-data", "no")
        with monkeypatch.context() as patched:
            patched.setattr("ens_evidence.incidents.store.record_history", failing)
            return cli("incident", "close", "INC-2026-001", *CLOSE, hours=3)

    def test_a_closure_without_history_says_what_is_written_and_what_is_missing(
        self, cli, world, monkeypatch
    ):
        result = self.cut_closure(cli, monkeypatch)

        assert result.exit_code == 1
        assert "No se registra" not in result.output
        assert "registro 2 (closed)" in result.output
        assert "está registrado, pero su historial no se generó" in result.output
        assert "el almacén no responde" in result.output
        assert "incident history INC-2026-001" in result.output
        assert len(stored(world)) == 4  # reserva y registro de cada orden

    def test_history_completes_the_incident_without_touching_the_records(
        self, cli, world, monkeypatch
    ):
        self.cut_closure(cli, monkeypatch)
        before = {key: world.store.read_bytes(key) for key in stored(world)}
        assert cli("incident", "verify").exit_code == 1

        result = cli("incident", "history", "INC-2026-001", "--by", "Ana", hours=4)

        assert result.exit_code == 0, result.output
        assert re.search(r"Historial: EV-\d{8}-[0-9a-f]{8}", result.output)
        assert "matrix link op.exp.9" in result.output
        assert {key: world.store.read_bytes(key) for key in stored(world)} == before
        assert cli("incident", "verify").exit_code == 0

    def test_history_refuses_an_open_a_complete_and_an_unknown_incident(self, cli):
        open_incident(cli, "--personal-data", "no")
        assert "no está cerrado" in cli("incident", "history", "INC-2026-001", "--by", "Ana").output
        close_incident(cli)

        complete = cli("incident", "history", "INC-2026-001", "--by", "Ana")
        missing = cli("incident", "history", "INC-2026-009", "--by", "Ana")

        assert complete.exit_code == missing.exit_code == 1
        assert "ya tiene historial" in complete.output
        assert "no existe" in missing.output

    def test_history_minimizes_the_declared_name(self, cli, monkeypatch):
        self.cut_closure(cli, monkeypatch)

        result = cli("incident", "history", "INC-2026-001", "--by", "ana@ejemplo.invalid")

        assert result.exit_code == 1
        assert "ana@ejemplo" not in result.output


class TestPresentation:
    def test_close_suggests_the_history_for_op_exp_9_and_op_exp_7(self, cli):
        open_incident(cli, "--personal-data", "no")
        output = close_incident(cli).output

        history = re.search(r"Historial: (EV-\S+)", output).group(1)
        assert f"matrix link op.exp.9 --evidence {history} --covers base" in output
        assert f"matrix link op.exp.7 --evidence {history} --covers op.exp.7.1" in output

    def test_show_speaks_plain_spanish_and_json_keeps_the_enums(self, cli):
        open_incident(cli, "--personal-data", "no")
        close_incident(cli)

        shown = cli("incident", "show", "INC-2026-001").output
        raw = json.loads(cli("incident", "show", "INC-2026-001", "--json").output)

        assert "Estado: cerrado" in shown and "reporte final" in shown
        assert "sin cambios en los eventos auditables" in shown
        assert not re.search(r"\b(closed|opened|updated|no_change|unknown)\b", shown)
        assert raw["records"][-1]["kind"] == "closed"

    def test_closing_without_a_personal_data_decision_says_what_to_do(self, cli):
        open_incident(cli)

        result = cli("incident", "close", "INC-2026-001", *CLOSE, hours=3)

        assert result.exit_code == 1
        assert "incident update INC-2026-001 --personal-data yes|no" in result.output
