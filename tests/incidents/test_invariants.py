"""Invariantes del registro de incidentes: lo que ningún camino puede romper."""

import pytest
import yaml
from matrix.acceptance_helpers import APPROVER
from matrix.evaluate_helpers import NOW
from orgdocs.orgdocs_helpers import SIGNATORY

from ens_evidence.incidents.model import IncidentState, check_successor
from ens_evidence.incidents.verify import scan_incident
from ens_evidence.scope.sync import SYNCED_KEYS, SYNCED_PREFIXES, synced_keys

from .acceptance_helpers import (
    CLOSE,
    NEW,
    PROCEDURE,
    PROCEDURE_MEASURE,
    REGISTER_MEASURE,
    SELF,
    Scenario,
    build_light,
    build_scenario,
    export,
    fix_owner_and_periodicity,
    row,
    run_simulation,
)

REAL = "INC-2026-001"
SIMULATION = "SIM-2026-001"
ARTICULADO_BEFORE_INCIDENTS = "e59affab976f6e6d1cf03cea8c7e53bd254bfc734cd16d9270f84f2f3d307b86"
POLICY = "politica-seguridad"
POLICY_CLAUSES = "org.1.2,org.1.3,org.1.4,org.1.5"
PERSONAL_OR_SECRET = {
    "correo": "lucia.perez@ejemplo.org",
    "dni": "12345678Z",
    "iban": "ES9121000418450200051332",
    "telefono": "+34 612 345 678",
    "secreto": "AKIAABCDEFGHIJKLMNOP",
}


@pytest.fixture
def scenario(tmp_path, monkeypatch) -> Scenario:
    return build_scenario(tmp_path, monkeypatch)


@pytest.fixture
def light(tmp_path, monkeypatch) -> Scenario:
    return build_light(tmp_path, monkeypatch)


# --- incidents/ no se sincroniza ---------------------------------------------------------------


def test_incidents_queda_fuera_de_las_claves_sincronizadas(scenario):
    run_simulation(scenario)

    assert scenario.store.list("incidents")
    assert not any(key.startswith("incidents") for key in SYNCED_KEYS)
    assert not any(prefix.startswith("incidents") for prefix in SYNCED_PREFIXES)
    assert not any(key.startswith("incidents") for key in synced_keys(scenario.store))


# --- simulated es inmutable --------------------------------------------------------------------


def test_simulated_no_puede_cambiar_entre_dos_registros_de_un_incidente(scenario):
    run_simulation(scenario)
    first, second, _ = (s.record for s in scan_incident(scenario.store, SIMULATION).records)

    flipped = second.model_copy(
        update={"state": second.state.model_copy(update={"simulated": False})}
    )

    assert check_successor(first, second) == []
    assert "simulated es inmutable" in check_successor(first, flipped)


@pytest.mark.parametrize(
    ("incident_id", "simulated"), [("SIM-2026-001", False), ("INC-2026-001", True)]
)
def test_el_prefijo_del_id_y_simulated_van_siempre_juntos(scenario, incident_id, simulated):
    run_simulation(scenario)
    state = scan_incident(scenario.store, SIMULATION).records[0].record.state.model_dump()

    with pytest.raises(ValueError, match="debe coincidir con `simulated`"):
        IncidentState.model_validate({**state, "id": incident_id, "simulated": simulated})


def test_un_simulacro_editado_a_real_a_mano_no_verifica(scenario):
    run_simulation(scenario)
    key = next(k for k in scenario.store.list(f"incidents/{SIMULATION}") if "/000002-" in k)
    scenario.store.write_text(
        key, scenario.store.read_text(key).replace("simulated: true", "simulated: false")
    )

    assert scenario.run("incident", "verify").exit_code == 1


# --- un rechazo no deja secretos ni identificadores personales ---------------------------------


def snapshot_after_opening(light: Scenario) -> dict[str, bytes]:
    opened = light.run(*NEW, *SELF, "--personal-data", "no")
    assert opened.exit_code == 0, opened.output
    return light.snapshot()


def close_with(conclusion: str) -> list[str]:
    options = list(CLOSE)
    options[options.index("--conclusion") + 1] = conclusion
    return options


REJECTED_COMMANDS = {
    "new --title": lambda v: ["incident", "new", "--title", v, *NEW[4:], *SELF],
    "new --summary": lambda v: [*NEW, "--summary", v, *SELF],
    "update --summary": lambda v: ["incident", "update", REAL, *SELF, "--summary", v],
    "update --action": lambda v: ["incident", "update", REAL, *SELF, "--action", v],
    "close --conclusion": lambda v: ["incident", "close", REAL, *close_with(v)],
}


@pytest.mark.parametrize("command", REJECTED_COMMANDS)
def test_control_el_mismo_comando_con_un_texto_inocuo_se_acepta(light, command):
    snapshot_after_opening(light)

    accepted = light.run(*REJECTED_COMMANDS[command]("Se aisla el servicio afectado"))

    assert accepted.exit_code == 0, accepted.output


@pytest.mark.parametrize("value", PERSONAL_OR_SECRET.values(), ids=PERSONAL_OR_SECRET.keys())
@pytest.mark.parametrize("command", REJECTED_COMMANDS)
def test_un_rechazo_no_escribe_nada_ni_deja_el_valor_en_evidence_raw(light, command, value):
    before = snapshot_after_opening(light)

    rejected = light.run(*REJECTED_COMMANDS[command](value))

    assert rejected.exit_code == 1, rejected.output
    assert value not in rejected.output
    assert light.snapshot() == before
    assert not any(value.encode() in artifact for artifact in light.raw_artifacts())


# --- ningún camino implanta sin revisión humana ------------------------------------------------


def test_ningun_camino_de_incidentes_implanta_op_exp_7_ni_op_exp_9_sin_revision(scenario):
    history = run_simulation(scenario)
    run = scenario.run
    fix_owner_and_periodicity(run)
    scenario.at(0)
    run("matrix", "link", REGISTER_MEASURE, "--evidence", history, "--covers", "base")
    run(
        "matrix",
        "link",
        PROCEDURE_MEASURE,
        "--document",
        PROCEDURE,
        "--covers",
        "op.exp.7.1,op.exp.7.2",
    )
    assert run(*NEW, *SELF, "--personal-data", "no").exit_code == 0
    assert run("incident", "close", REAL, *CLOSE).exit_code == 0
    for command in (
        ("incident", "verify"),
        ("incident", "list"),
        ("docs", "build"),
        ("matrix", "status"),
    ):
        run(*command)

    document = export(run)

    for measure in (PROCEDURE_MEASURE, REGISTER_MEASURE):
        assert row(document, measure)["implemented"] is False
        assert row(document, measure)["last_review"] is None
        assert ("MISSING_REVIEW", "review_missing") in {
            (b["kind"], b["detail"]) for b in row(document, measure)["blockers"]
        }
    assert [k for k in scenario.store.list("controls/reviews") if "op.exp." in k] == []


# --- las aprobaciones anteriores a la ampliación del articulado --------------------------------


def age_approvals(scenario: Scenario) -> None:
    """Las cinco aprobaciones antiguas, hechas contra el articulado anterior a esta ampliación."""
    for name in (n for n in APPROVER if n != PROCEDURE):
        (key,) = scenario.store.list(f"org/docs/approvals/{name}/")
        record = yaml.safe_load(scenario.store.read_text(key))
        record["articulado_sha256"] = ARTICULADO_BEFORE_INCIDENTS
        scenario.store.write_text(key, yaml.safe_dump(record))


def policy_counts(scenario: Scenario) -> bool:
    return row(export(scenario.run), "org.1")["manual"][0]["counts"]


def test_las_cinco_aprobaciones_antiguas_pasan_a_obsoletas_y_se_recuperan_al_aprobar_de_nuevo(
    scenario,
):
    run = scenario.run
    scenario.at(0)
    fix_owner_and_periodicity(run)
    assert (
        run(
            "matrix", "set", "org.1", "--owner", "Ana", "--owner-role", "system_administrator"
        ).exit_code
        == 0
    )
    assert (
        run("matrix", "link", "org.1", "--document", POLICY, "--covers", POLICY_CLAUSES).exit_code
        == 0
    )
    assert policy_counts(scenario) is True

    age_approvals(scenario)
    obsolete = run("docs", "status")

    assert obsolete.exit_code == 1
    assert obsolete.output.count("aprobado pero obsoleto") == 5
    assert f"{PROCEDURE}: aprobado y vigente" in obsolete.output
    assert policy_counts(scenario) is False

    assert run("docs", "build").exit_code == 0
    scenario.clock.now = NOW
    for name, role in APPROVER.items():
        if name != PROCEDURE:
            approved = run("docs", "approve", name, "--by", SIGNATORY[role], "--role", role.value)
            assert approved.exit_code == 0, approved.output

    assert run("docs", "status").exit_code == 0
    assert policy_counts(scenario) is True
