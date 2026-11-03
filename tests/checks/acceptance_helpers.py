"""Mundo de aceptación del motor de checks: un workspace real, manejado solo por la CLI.

Parte de la Declaración de Aplicabilidad firmada (69 medidas aplicables). Todas llevan responsable,
periodicidad y evidencia manual; `op.exp.1` se sustenta solo con el check
`github.secret_scanning_enabled`. Lo único que no pasa por la CLI es la recogida de GitHub, que en
producción hace el collector con red: aquí se importa el resultado ya recogido.

Construir el mundo cuesta cientos de invocaciones; se hace una vez por sesión y cada test trabaja
sobre una copia.
"""

import atexit
import functools
import json
import shutil
import tempfile
from datetime import timedelta
from pathlib import Path

import pytest
import yaml
from applicability.helpers import (
    LATER,
    categorize,
    decide,
    decide_all_undetermined,
    prepared_workspace,
    uniform,
)
from applicability.helpers import (
    NOW as APPROVAL_DAY,
)
from incidents.acceptance_helpers import PROCEDURE, REGISTER_MEASURE, Scenario, run_simulation
from matrix.acceptance_helpers import (
    APPROVER,
    SELF,
    Clock,
    configure_measure,
    import_manual_evidence,
    link,
    review,
    runner,
    settings_file,
)
from matrix.evaluate_helpers import NOW
from matrix.helpers import COMPENSATION, SIGNED_AT
from orgdocs.orgdocs_helpers import SIGNATORY, fixture_answers, write_answers
from scope.scope_helpers import edit

from ens_evidence.applicability import approve_declaration, propose_applicability
from ens_evidence.checks.store import scan_runs
from ens_evidence.evidence.importer import import_collected
from ens_evidence.matrix import plan_obligations
from ens_evidence.models.enums import Applicability
from ens_evidence.orgdocs import approve_document, build_documents
from ens_evidence.workspace import LocalStore

from .test_expected import COLLECTED, gh_evidence

SECRET = "github.secret_scanning_enabled"
CHECK_MEASURE = "op.exp.1"
SHORT = "7"  # la medida sustentada solo por el check caduca pronto
LIVE = {"facturas": False, "infra": False}
GITHUB_CONFIG = {"asset_id": "gh-taller", "organization": "taller-norte"}
CHECKED_AT = NOW - timedelta(hours=1)
REVIEWED_AT = NOW - timedelta(minutes=30)
PENDING = "Revisión humana pendiente"
CHECKS_DIR = "evidence/checks/github"


class World:
    """Un workspace copiado, su CLI y su reloj."""

    def __init__(self, path: Path, settings: Path, clock: Clock) -> None:
        self.store = LocalStore(path)
        self.settings = settings
        self.clock = clock
        self.run = runner(settings)

    def at(self, instant) -> None:
        self.clock.now = instant

    def collect(self, *, secret_scanning_off: bool = False, at=COLLECTED, repos=LIVE) -> None:
        collect(self.store, secret_scanning_off=secret_scanning_off, at=at, repos=repos)

    def check_all(self, at=CHECKED_AT):
        self.at(at)
        return self.run("check", "--all")

    def check_all_corrected(self, at=CHECKED_AT):
        self.at(at)
        return self.run("check", "--all", "--clock-corrected")

    def status(self, at=NOW, *args: str):
        return self.run("assessment", "status", "--at", at.isoformat(), *args)

    def blockers(self, at=NOW) -> list[dict]:
        """Los bloqueos del JSON de `assessment status`, tal como los ve quien lo consume."""
        return json.loads(self.status(at, "--format", "json").output)["blockers"]

    def blockers_of(self, measure: str, at=NOW) -> set[tuple[str, str]]:
        return {(b["kind"], b["detail"]) for b in self.blockers(at) if b["measure_id"] == measure}

    def register_repository_and_reapprove(self, at):
        """Alta de un repo en el inventario y todo lo que cuelga del alcance, vuelto a aprobar.

        Devuelve el instante desde el que la Declaración vuelve a estar vigente.
        """
        self.at(at)
        edit(self.store, "assets", "repos: [facturas, infra]", "repos: [facturas, infra, nuevo]")
        approved = self.run("scope", "approve", "--by", "Ana Ruiz")
        assert approved.exit_code == 0, approved.output
        categorize(self.store, uniform("MEDIO"), APPROVAL_DAY)  # el alcance nuevo la deja obsoleta
        _declare(self.store, at + timedelta(hours=1))
        return at + timedelta(hours=2)

    def collector_manifest(self) -> str:
        """Clave del manifiesto de la última evidencia de GitHub importada."""
        found = [
            key
            for key in self.store.list("evidence/manifests")
            if "source: github" in self.store.read_text(key)
        ]
        return max(found, key=lambda key: yaml.safe_load(self.store.read_text(key))["collected_at"])

    def runs(self) -> list[str]:
        return [r.key for r in scan_runs(self.store).runs.get("github", [])]

    def check_keys(self) -> list[str]:
        return self.store.list("evidence/checks")


def collect(store, *, secret_scanning_off=False, at=COLLECTED, repos=LIVE, repo="infra") -> None:
    evidence = gh_evidence(repos, collected_at=at)
    if secret_scanning_off:
        for item in evidence.data["sections"]["secret_scanning"]["items"]:
            if item["repository"] == repo:
                item["state"] = "disabled"
    import_collected(store, evidence, collector_version="1", now=at + timedelta(minutes=1))


def _declare(store, signed_at, proposed_at=LATER) -> None:
    propose_applicability(store, proposed_at)
    decide_all_undetermined(store)
    decide(store, "op.pl.1", Applicability.APPLICABLE, "Compensada.", compensation=COMPENSATION)
    decide(store, "op.cont.2", Applicability.APPLICABLE, "Seleccionada como adicional.")
    approve_declaration(store, "Luis Pardo", signed_at)


def _signed_workspace(path: Path, level: str = "MEDIO") -> LocalStore:
    store = prepared_workspace(path / "ws", uniform(level))
    _declare(store, SIGNED_AT)
    return store


def _approve_incident_procedure(store: LocalStore) -> None:
    """op.exp.9 exige el procedimiento de incidentes aprobado antes de cerrar un incidente."""
    write_answers(store, fixture_answers())
    build_documents(store)
    role = APPROVER[PROCEDURE]
    approve_document(store, PROCEDURE, SIGNATORY[role], role, NOW - timedelta(days=1))


def _cli(path: Path, monkeypatch, at) -> tuple[Clock, object]:
    clock = Clock(monkeypatch)
    clock.now = at
    settings = settings_file(path, {"backend": "local", "local": {"path": str(path / "ws")}})
    return clock, runner(settings)


def _build_linked(path: Path, level: str = "MEDIO") -> None:
    """Medidas configuradas y enlazadas, sin revisar y sin recogida ni ejecución."""
    from checks.test_engine import write_config

    store = _signed_workspace(path, level)
    _approve_incident_procedure(store)
    write_config(store, "github", GITHUB_CONFIG)
    with pytest.MonkeyPatch.context() as monkeypatch:
        clock, run = _cli(path, monkeypatch, NOW - timedelta(hours=2))
        assert run("matrix", "init").exit_code == 0
        evidence_id = import_manual_evidence(run, path)
        incident_history = run_simulation(Scenario(store, path, run, clock))
        clock.now = NOW - timedelta(hours=2)
        rows = plan_obligations(store, NOW).rows
        assert len(rows) >= 69
        for row in rows:
            configure_measure(
                run, row.measure_id, every=SHORT if row.measure_id == CHECK_MEASURE else "365"
            )
            if row.kind.value != "selected":
                continue
            if row.measure_id == CHECK_MEASURE:
                link(run, row.measure_id, "check", SECRET, "base")
            elif row.measure_id == REGISTER_MEASURE:  # solo admite el historial de un incidente
                link(run, row.measure_id, "evidence", incident_history, "base")
            else:
                covers = ",".join(o.id for o in row.obligations)
                link(run, row.measure_id, "evidence", evidence_id, covers)


def _build_ready(path: Path, linked: Path) -> None:
    """Recogida sana, `check --all` en PASS y una revisión favorable por medida."""
    shutil.copytree(linked, path)
    _repoint(path)
    collect(LocalStore(path / "ws"))
    with pytest.MonkeyPatch.context() as monkeypatch:
        clock, run = _cli(path, monkeypatch, CHECKED_AT)
        checked = run("check", "--all")
        assert checked.exit_code == 0, checked.output
        clock.now = REVIEWED_AT
        for row in plan_obligations(LocalStore(path / "ws"), NOW).rows:
            if row.kind.value == "selected":
                reviewed = review(run, row.measure_id)
            else:  # decisión de no aplicabilidad: la revisa una persona
                reviewed = run(
                    "matrix", "review", row.measure_id, *SELF, "--conclusion", "Revisada"
                )
            assert reviewed.exit_code == 0, (row.measure_id, reviewed.output)


def _repoint(path: Path) -> None:
    """El settings de la copia apunta a la copia, no al original."""
    settings_file(path, {"backend": "local", "local": {"path": str(path / "ws")}})


@functools.cache
def _template(kind: str, level: str = "MEDIO") -> Path:
    root = Path(tempfile.mkdtemp(prefix="ens-acceptance-"))
    atexit.register(shutil.rmtree, root, ignore_errors=True)
    linked = root / "linked"
    linked.mkdir()
    _build_linked(linked, level)
    if kind == "linked":
        return linked
    ready = root / "ready"
    _build_ready(ready, linked)
    return ready


def make_world(tmp_path: Path, monkeypatch, kind: str, at=NOW, level: str = "MEDIO") -> World:
    """`linked`: enlazado, sin ejecuciones. `ready`: además recogida, PASS y revisiones.

    `level` es el nivel uniforme de la categorización (MEDIO por defecto; BAJO = BÁSICA).
    """
    destination = tmp_path / "world"
    shutil.copytree(_template(kind, level), destination)
    settings = settings_file(
        destination, {"backend": "local", "local": {"path": str(destination / "ws")}}
    )
    clock = Clock(monkeypatch)
    clock.now = at
    return World(destination / "ws", settings, clock)
