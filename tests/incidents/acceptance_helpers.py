"""Instrumentos de la aceptación del registro de incidentes: un workspace BÁSICA real.

Alcance aprobado, Declaración vigente con op.exp.7 y op.exp.9, respuestas completas y los org-docs
reales generados. Sin fuentes falsas: el procedimiento se aprueba con el flujo de producción.
"""

import json
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from applicability.helpers import (
    LATER,
    decide_all_undetermined,
    prepared_workspace,
    uniform,
)
from matrix.acceptance_helpers import (
    APPROVER,
    EVIDENCE_ID,
    Clock,
    configure_measure,
    runner,
    settings_file,
)
from matrix.evaluate_helpers import NOW
from matrix.helpers import SIGNED_AT
from orgdocs.orgdocs_helpers import SIGNATORY, fixture_answers, write_answers

from ens_evidence.applicability import approve_declaration, propose_applicability
from ens_evidence.orgdocs import approve_document, build_documents
from ens_evidence.workspace import LocalStore

PROCEDURE = "procedimiento-incidentes"
PROCEDURE_MEASURE = "op.exp.7"
REGISTER_MEASURE = "op.exp.9"
APPROVED_AT = NOW - timedelta(days=1)
DETECTED = NOW - timedelta(hours=6)
NEW = ["incident", "new", "--title", "Acceso indebido", "--detected-at", DETECTED.isoformat()]
SELF = ["--by", "Ana"]
CLOSE = [
    *("--conclusion", "Contenido el acceso y rotadas las credenciales de servicio."),
    *("--auditable-events-result", "no_change"),
    *("--auditable-events-detail", "Los eventos ya cubrian el caso."),
    *("--legal-relevance", "no"),
    *SELF,
]


@dataclass
class Scenario:
    """El workspace de la aceptación y la CLI que lo gobierna, con el reloj en NOW - 5 h."""

    store: LocalStore
    path: Path
    run: object
    clock: Clock

    def at(self, hours_before_now: float) -> None:
        self.clock.now = NOW - timedelta(hours=hours_before_now)

    def snapshot(self) -> dict[str, bytes]:
        return {key: self.store.read_bytes(key) for key in self.store.list("")}

    def raw_artifacts(self) -> list[bytes]:
        return [self.store.read_bytes(key) for key in self.store.list("evidence/raw")]


def build_scenario(tmp_path: Path, monkeypatch, *, approve_procedure: bool = True) -> Scenario:
    """Workspace BÁSICA listo para `incident …` y `matrix …`: la matriz recién iniciada."""
    path = tmp_path / "ws"
    store = prepared_workspace(path, uniform("BAJO"))
    propose_applicability(store, LATER)
    decide_all_undetermined(store)
    approve_declaration(store, "Luis Pardo", SIGNED_AT)
    write_answers(store, fixture_answers())
    build_documents(store)
    for name, role in APPROVER.items():
        if approve_procedure or name != PROCEDURE:
            approve_document(store, name, SIGNATORY[role], role, APPROVED_AT)
    clock = Clock(monkeypatch)
    clock.now = NOW - timedelta(hours=5)
    run = runner(settings_file(tmp_path, {"backend": "local", "local": {"path": str(path)}}))
    scenario = Scenario(store, path, run, clock)
    assert run("matrix", "init").exit_code == 0
    return scenario


def build_light(tmp_path: Path, monkeypatch) -> Scenario:
    """Un workspace vacío con la CLI y el reloj en NOW - 5 h: basta para incidentes reales."""
    path = tmp_path / "ws"
    clock = Clock(monkeypatch)
    clock.now = NOW - timedelta(hours=5)
    settings = settings_file(tmp_path, {"backend": "local", "local": {"path": str(path)}})
    return Scenario(LocalStore(path), path, runner(settings), clock)


def fix_owner_and_periodicity(run) -> None:
    """Responsable y periodicidad de op.exp.7 y op.exp.9 (`matrix set`)."""
    for measure in (PROCEDURE_MEASURE, REGISTER_MEASURE):
        configure_measure(run, measure, every="365")


def evidence_id(output: str) -> str:
    match = EVIDENCE_ID.search(output)
    assert match, output
    return match.group()


def history_id(output: str) -> str:
    """El EV del historial que imprime `incident close` (línea «Historial:»)."""
    line = next(line for line in output.splitlines() if line.strip().startswith("Historial:"))
    return evidence_id(line)


def export(run) -> dict:
    result = run("matrix", "export", "--stdout")
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def row(document: dict, measure: str) -> dict:
    return next(r for r in document["rows"] if r["measure_id"] == measure)


def blocker_details(document_row: dict) -> set[tuple[str, str]]:
    return {(b["kind"], b["detail"]) for b in document_row["blockers"]}


def run_simulation(scenario: Scenario) -> str:
    """`incident new --simulated`, `update` con una actuación de emergencia y `close`.

    Devuelve el EV del historial. El reloj queda tras el cierre, antes de NOW.
    """
    run = scenario.run
    scenario.at(5)
    opened = run(*NEW, "--simulated", *SELF)
    assert opened.exit_code == 0, opened.output
    scenario.at(4)
    updated = run(
        *("incident", "update", "SIM-2026-001", *SELF),
        *("--action", "Se aísla el servicio de pruebas"),
        *("--action-kind", "emergency"),
        *("--personal-data", "no"),
    )
    assert updated.exit_code == 0, updated.output
    scenario.at(3)
    closed = run("incident", "close", "SIM-2026-001", *CLOSE)
    assert closed.exit_code == 0, closed.output
    return history_id(closed.output)
