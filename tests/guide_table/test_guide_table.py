"""Huecos de la tabla de validación de la guía (docs/ens-compliance-guide.md:965-975).

Cada test recorre la CLI con datos ficticios, fixtures y sin red. El resto de filas de la tabla
ya tienen sus tests: ver docs/validation/matriz-de-pruebas.md.
"""

import json
from datetime import timedelta

import httpx
import pytest
from assessment.dossier.stages import AFTER, basic
from checks.acceptance_helpers import CHECK_MEASURE
from connectors.github.cli_support import runner, seed, write_settings
from connectors.github.fake_api import NOW as API_NOW
from connectors.github.fake_api import TOKEN, FakeApi
from matrix.acceptance_helpers import review
from preaudit.helpers import FakeAnthropic, configure, prepared_sha, world_at
from reports.acceptance_fakes import COLLECTED_AT_ENV
from reports.acceptance_helpers import START, Tick, connector, cycle_world, daily_json

from ens_evidence.cli import app
from ens_evidence.connectors import ConnectorRegistry
from ens_evidence.connectors.github import GitHubConnector
from ens_evidence.connectors.github.connector import TOKEN_ENV
from ens_evidence.evidence import list_manifests
from ens_evidence.scheduling.collect import Plugins
from ens_evidence.workspace import LocalStore, StorageUnavailable
from guide_table.fakes import UNPROTECTED_ENV, GitHubBranchSwitch

BRANCH_CHECK = "github.default_branch_protected"
STORAGE_DOWN = StorageUnavailable("almacenamiento no disponible")


# --- Storage caído: «Collection no OK» -----------------------------------------------------------


@pytest.fixture
def github_workspace(tmp_path, monkeypatch):
    """Workspace local con el alcance de ejemplo y un conector de GitHub sobre la API simulada."""
    store = seed(LocalStore(tmp_path / "ws"))
    settings = write_settings(
        tmp_path, {"backend": "local", "local": {"path": str(tmp_path / "ws")}}
    )

    class Injected(GitHubConnector):
        def __init__(self) -> None:
            super().__init__(
                transport=FakeApi().transport, clock=lambda: API_NOW, env={TOKEN_ENV: TOKEN}
            )

    registry = ConnectorRegistry()
    registry.register(Injected)
    monkeypatch.setattr("ens_evidence.cli.connector_registry", lambda: registry)
    monkeypatch.setattr("ens_evidence.cli.clock", lambda: API_NOW)
    monkeypatch.setenv("ENS_STATE_DIR", str(tmp_path / "state"))
    return settings, store


def ens(settings, *args: str):
    return runner.invoke(app, ["--settings", str(settings), *args])


def fail_writes(monkeypatch) -> None:
    def down(self, key, data):
        raise STORAGE_DOWN

    monkeypatch.setattr(LocalStore, "create_bytes", down)
    monkeypatch.setattr(LocalStore, "write_bytes", down)


def test_con_el_storage_caido_la_recogida_sale_con_error_y_no_registra_ninguna_evidencia(
    github_workspace, monkeypatch
):
    settings, store = github_workspace

    with monkeypatch.context() as storage_down:
        fail_writes(storage_down)
        result = ens(settings, "collect", "github")

    assert result.exit_code == 2, result.output
    assert "almacenamiento no disponible" in result.output
    assert "Evidencia registrada" not in result.output
    assert list_manifests(store).manifests == []
    assert store.list("evidence") == []


def test_tras_una_recogida_con_el_storage_caido_check_no_da_pass(github_workspace, monkeypatch):
    settings, store = github_workspace
    with monkeypatch.context() as storage_down:
        fail_writes(storage_down)
        ens(settings, "collect", "github")

    checked = ens(settings, "check", "--all")

    assert checked.exit_code != 0, checked.output
    assert "PASS" not in checked.output.replace("Un check en PASS no da por implantada", "")


# --- LLM caído: «Checks siguen» ------------------------------------------------------------------


class Unreachable(FakeAnthropic):
    """La Messages API sin respuesta: la conexión falla o se agota el tiempo."""

    def __init__(self, error: Exception) -> None:
        super().__init__(reply=lambda payload: {"findings": []})
        self.error = error

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        raise self.error


@pytest.fixture(scope="module")
def stages():
    return basic()


@pytest.fixture
def preaudit_world(stages, tmp_path, monkeypatch):
    world = world_at(stages, tmp_path, monkeypatch)
    world.at(AFTER)
    return world


@pytest.mark.parametrize(
    "error",
    [httpx.ConnectError("sin ruta"), httpx.ReadTimeout("sin respuesta")],
    ids=["ConnectError", "ReadTimeout"],
)
def test_con_el_proveedor_caido_la_capa_determinista_termina_y_la_de_juicio_da_error(
    preaudit_world, stages, monkeypatch, error
):
    world = preaudit_world
    configure(world)
    fake = Unreachable(error).install(monkeypatch)
    prepared = world.run("preaudit", "prepare", stages.dossier)
    assert prepared.exit_code == 0, prepared.output
    world.at(AFTER + timedelta(minutes=1))

    sent = world.run(
        *("preaudit", "run", stages.dossier),
        *("--consent", prepared_sha(prepared.output), "--by", "Ana"),
    )

    assert fake.requests, "el proveedor no se llegó a invocar"
    assert sent.exit_code == 1, sent.output
    assert "Capa determinista: DONE" in sent.output
    assert "Capa de juicio: ERROR" in sent.output
    assert "Revisión humana pendiente" in sent.output
    assert "(JUDGMENT)" not in world.run("preaudit", "show", "1").output
    status = json.loads(
        world.run(
            *("assessment", "status", "--format", "json", "--at"),
            (AFTER + timedelta(minutes=2)).isoformat(),
        ).output
    )
    assert status["pending_human_review"] is True
    assert status["pre_audit"].endswith("capa de juicio con error, sin hallazgos.")


# --- Quitar protección: «Detectar desviación» ----------------------------------------------------

CYCLE = ("report", "daily", "--cycle")


def branch_plugins() -> Plugins:
    registry = ConnectorRegistry()
    registry.register(GitHubBranchSwitch)
    return Plugins(registry)


def cycle_at(world, monkeypatch, start):
    monkeypatch.setenv(COLLECTED_AT_ENV, (start - timedelta(minutes=1)).isoformat())
    world.tick = Tick(start)
    monkeypatch.setattr("ens_evidence.cli.clock", world.tick)
    return world.run(*CYCLE)


def removed_protection(tmp_path, monkeypatch):
    world = cycle_world(tmp_path, monkeypatch, {"github": "ok"}, extra=())
    monkeypatch.setattr("ens_evidence.cli.load_plugins", branch_plugins)
    linked = world.run(
        *("matrix", "link", CHECK_MEASURE, "--check", BRANCH_CHECK, "--covers", "base")
    )
    assert linked.exit_code == 0, linked.output
    assert cycle_at(world, monkeypatch, START).exit_code in (0, 3)
    world.at(START + timedelta(minutes=30))
    assert review(world.run, CHECK_MEASURE).exit_code == 0
    world.before = world.status(START + timedelta(minutes=31))

    monkeypatch.setenv(UNPROTECTED_ENV, "1")
    day2 = START + timedelta(days=1)
    world.cycle = cycle_at(world, monkeypatch, day2)
    world.day2 = day2
    return world


@pytest.fixture(scope="module")
def protected_then_removed(tmp_path_factory):
    """Rama protegida y preparación READY el día 1; el día 2, otra recogida sin la protección."""
    with pytest.MonkeyPatch.context() as monkeypatch:
        yield removed_protection(tmp_path_factory.mktemp("branch"), monkeypatch)


def test_antes_de_quitarla_la_preparacion_es_ready(protected_then_removed):
    assert protected_then_removed.before.exit_code == 0, protected_then_removed.before.output


def test_la_proteccion_quitada_da_fail_en_check(protected_then_removed):
    world = protected_then_removed

    before = connector(daily_json(world.store, "2026-10-30"), "github")["checks"]["value"]
    after = connector(daily_json(world.store, "2026-10-31"), "github")["checks"]["value"]
    assert before["counts"]["FAIL"] == 0 and before["counts"]["PASS"] > 0
    assert after["counts"]["FAIL"] == 1


def test_el_cambio_de_pass_a_fail_aparece_en_el_informe_diario(protected_then_removed):
    world = protected_then_removed

    document = daily_json(world.store, "2026-10-31")

    changes = document["changes"]["value"]["items"]
    assert changes[0]["change"] == "added"
    assert f"el check {BRANCH_CHECK} ha fallado en «infra»" in changes[0]["subject"]
    assert {"change": "changed", "subject": "connector: github"} in [
        {k: c[k] for k in ("change", "subject")} for c in changes
    ]


def test_la_proteccion_quitada_deja_la_preparacion_bloqueada(protected_then_removed):
    world = protected_then_removed

    status = world.status(world.day2 + timedelta(minutes=5))

    assert status.exit_code == 1, status.output
    assert "Preparación: no preparada" in status.output
    assert ("NOT_COMPLIANT", f"check_failed:{BRANCH_CHECK}:infra") in world.blockers_of(
        CHECK_MEASURE, world.day2 + timedelta(minutes=5)
    )
