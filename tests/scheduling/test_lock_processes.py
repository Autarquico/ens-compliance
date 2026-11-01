"""El lock del ciclo con procesos reales y el ciclo completo con copia.

Dos procesos `spawn` llaman a `report daily --cycle` con el conector falso de `collect_fakes`;
el propietario se queda colgado en su recogida mientras el segundo intenta entrar. Después, el
ciclo completo con un restic falso: sin autobloqueo, y con dos ficheros de settings distintos
sobre el mismo destino que comparten el mismo lock.
"""

import contextlib
import multiprocessing
import os
import signal
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from ens_evidence.backup import PASSWORD_VAR, list_runs
from ens_evidence.cli import app
from ens_evidence.models.enums import CollectionStatus
from ens_evidence.reports.model import DailyReport
from ens_evidence.reports.store import cycle_finished, load_json_strict, pairs, run_ids, stages_of
from ens_evidence.scheduling.calendar import Job
from ens_evidence.scheduling.config import CollectConfig, ScheduleConfig
from ens_evidence.scheduling.lock import CycleBusy, CycleLock
from ens_evidence.scheduling.scheduler import CycleExecutor, JobResult
from ens_evidence.workspace.settings import load_settings
from scheduling import test_collect, test_cycle
from scheduling.test_cycle import STAGES, TickClock

store = test_collect.store
fake = test_collect.fake
plugins = test_collect.plugins
settings = test_cycle.settings
base_env = test_cycle.env
restic = test_cycle.restic
context = test_cycle.context
scripted_restic = test_cycle.scripted_restic

_SPAWN = multiprocessing.get_context("spawn")
NOW = datetime(2026, 11, 2, 7, 0, tzinfo=UTC)
JOB = Job("daily", "2026-11-02", datetime(2026, 11, 2, 5, 0, tzinfo=UTC))
DAILY_CYCLE = ("report", "daily", "--cycle")


def _report_cycle(settings_file: str, queue) -> None:
    """Cuerpo de un proceso: `report daily --cycle` con el conector falso como único plugin."""
    from ens_evidence import cli
    from ens_evidence.connectors.registry import ConnectorRegistry
    from ens_evidence.scheduling.collect import Plugins
    from scheduling.collect_fakes import FakeConnector

    registry = ConnectorRegistry()
    registry.register(FakeConnector)
    cli.load_plugins = lambda: Plugins(registry)
    cli.connector_registry = lambda: registry
    result = CliRunner().invoke(app, ["--settings", settings_file, *DAILY_CYCLE])
    queue.put((result.exit_code, result.output))


def _wait_for(path: Path, seconds: float = 60) -> None:
    deadline = time.monotonic() + seconds
    while not path.exists():
        assert time.monotonic() < deadline, f"{path.name} no apareció"
        time.sleep(0.05)


def _kill(pid: int) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, signal.SIGKILL)


@pytest.fixture
def owner(settings_path, fake):
    """Un proceso que ya es propietario del ciclo: su collector está colgado.

    Se mata al terminar, junto con el collector (que no muere con su padre).
    """
    fake("hang")
    queue = _SPAWN.Queue()
    process = _SPAWN.Process(target=_report_cycle, args=(str(settings_path), queue))
    process.start()
    _wait_for(fake.state / "pid")
    collector = int((fake.state / "pid").read_text())
    yield process
    _kill(process.pid)
    _kill(collector)
    process.join(30)


@pytest.fixture
def settings_path(settings, tmp_path):
    """El fichero que escribe `settings`: con la copia configurada."""
    return tmp_path / "settings.yaml"


def snapshot(store) -> dict[str, bytes]:
    return {key: store.read_bytes(key) for key in store.list("") if key}


def test_el_segundo_proceso_que_llama_a_report_daily_cycle_sale_con_2_sin_escribir(
    owner, settings_path, store
):
    before = snapshot(store)
    queue = _SPAWN.Queue()
    second = _SPAWN.Process(target=_report_cycle, args=(str(settings_path), queue))

    second.start()
    second.join(60)
    exit_code, output = queue.get(timeout=5)

    assert second.exitcode == 0 and owner.is_alive()
    assert exit_code == 2
    assert "ciclo en curso" in output
    assert snapshot(store) == before


def test_tras_un_sigkill_del_propietario_el_lock_queda_libre_y_el_siguiente_ciclo_corre(
    owner, settings, settings_path, store, fake, plugins, monkeypatch
):
    with pytest.raises(CycleBusy):
        CycleLock.for_settings(settings).acquire()

    os.kill(owner.pid, signal.SIGKILL)
    owner.join(30)
    assert owner.exitcode == -signal.SIGKILL

    with CycleLock.for_settings(settings):
        pass
    assert any(not cycle_finished(store, run) for run in run_ids(store))  # el ciclo quedó a medias
    fake("ok")
    monkeypatch.setattr("ens_evidence.cli.load_plugins", lambda: plugins)
    monkeypatch.setattr("ens_evidence.cli.connector_registry", lambda: plugins.registry)
    result = CliRunner().invoke(app, ["--settings", str(settings_path), *DAILY_CYCLE])
    assert result.exit_code != 2, result.output  # 2 sería «hay un ciclo en curso»
    finished = [run for run in run_ids(store) if cycle_finished(store, run)]
    assert len(finished) == 1 and pairs(store, "daily", "2026-11-01")[0].published


def test_un_collector_bloqueado_acaba_en_timeout_y_error_y_el_ciclo_sigue(
    store, settings, env, context, plugins, fake, restic
):
    fake("hang")
    patient = CollectConfig(retries=0).model_copy(update={"timeout_minutes": 0.03})
    cfg = ScheduleConfig(collect=patient)
    outcome = test_cycle.cycle(store, settings, env, context, plugins, TickClock(), cfg=cfg)

    pid = int((fake.state / "pid").read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    (collected,) = outcome.collected
    assert collected.result.data["phase"] == "timeout"
    assert collected.result.collection_status is CollectionStatus.ERROR
    assert outcome.completed and stages_of_names(store, outcome.run_id) == STAGES
    (pair,) = pairs(store, "daily", "2026-11-01")
    report = DailyReport.model_validate(load_json_strict(store.read_bytes(pair.json_key)))
    (github,) = [entry for entry in report.connectors if entry.connector == "github"]
    assert github.collection.value is CollectionStatus.ERROR


def stages_of_names(store, run_id) -> list[str]:
    return [m.stage for m in stages_of(store, run_id).stages]


def test_collect_y_check_all_comparten_el_lock_con_el_propietario_colgado(
    owner, settings_path, store
):
    runner = CliRunner()

    for args in (("collect", "--all"), ("check", "--all")):
        result = runner.invoke(app, ["--settings", str(settings_path), *args])
        assert result.exit_code == 2, (args, result.output)


# --- ciclo completo con copia -------------------------------------------------------------------


@pytest.fixture
def env(base_env, fake):
    """El entorno del planificador comparte el directorio de estado del lock con el de la prueba."""
    return {**base_env, "ENS_STATE_DIR": os.environ["ENS_STATE_DIR"]}


@pytest.fixture
def full_cli(settings_path, settings, plugins, fake, restic, monkeypatch):
    """`report daily --cycle` por la CLI con settings que incluyen la copia y un restic falso."""
    fake("ok")
    monkeypatch.setenv(PASSWORD_VAR, test_cycle.PASSWORD)
    monkeypatch.setattr("ens_evidence.cli.clock", lambda: NOW)
    monkeypatch.setattr("ens_evidence.cli.load_plugins", lambda: plugins)
    monkeypatch.setattr("ens_evidence.cli.connector_registry", lambda: plugins.registry)

    def run(settings_file, *args: str):
        return CliRunner().invoke(app, ["--settings", str(settings_file), *args])

    return run


def test_el_ciclo_completo_con_copia_por_la_cli_no_se_autobloquea(
    full_cli, settings_path, settings, store, restic
):
    result = full_cli(settings_path, *DAILY_CYCLE)

    assert result.exit_code != 2, result.output  # los datos del conector falso dan checks en ERROR
    (run_id,) = run_ids(store)
    assert stages_of_names(store, run_id) == STAGES
    (record,) = list_runs(store)[0]
    assert record.status == "completed" and restic.calls()
    with CycleLock.for_settings(settings):  # la CLI soltó el lock al terminar
        pass


def test_el_ciclo_completo_con_copia_por_el_planificador_no_se_autobloquea(
    store, settings, env, plugins, fake, restic
):
    fake("ok")
    stop = threading.Event()
    clock = TickClock(JOB.due_at + timedelta(minutes=1))
    executor = CycleExecutor(store, settings, env, clock, stop, plugins=plugins)

    result = executor.run(JOB, ScheduleConfig(), "scheduled")

    assert result == JobResult("completed")
    (run_id,) = run_ids(store)
    assert stages_of_names(store, run_id) == STAGES
    assert restic.calls()
    with CycleLock.for_settings(settings):
        pass


@pytest.fixture
def other_settings(settings, tmp_path):
    """Otro fichero de settings sobre el mismo workspace: ruta con enlace simbólico y otro destino
    de copia."""
    link = tmp_path / "enlace-al-workspace"
    link.symlink_to(settings.workspace.local.path)
    path = tmp_path / "otros-settings.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "workspace": {"backend": "local", "local": {"path": str(link)}},
                "backup": {"restic_repository": str(tmp_path / "otro-repo")},
            }
        )
    )
    return path


def test_dos_settings_distintos_sobre_el_mismo_destino_comparten_el_lock(
    full_cli, settings_path, other_settings, settings, store, restic
):
    other = load_settings(other_settings, allow_inside_product=True)
    assert other != settings

    with CycleLock.for_settings(settings):
        busy = full_cli(other_settings, *DAILY_CYCLE)
    with CycleLock.for_settings(other):
        busy_the_other_way = full_cli(settings_path, *DAILY_CYCLE)

    assert busy.exit_code == busy_the_other_way.exit_code == 2
    assert run_ids(store) == [] and restic.calls() == []


def test_el_planificador_con_otros_settings_del_mismo_destino_ve_el_lock_ocupado(
    settings, other_settings, store, env, plugins, fake
):
    fake("ok")
    other = load_settings(other_settings, allow_inside_product=True)
    executor = CycleExecutor(
        store, other, env, TickClock(JOB.due_at), threading.Event(), plugins=plugins,
        lock_wait_minutes=0,
    )  # fmt: skip

    with CycleLock.for_settings(settings):
        result = executor.run(JOB, ScheduleConfig(), "scheduled")

    assert result == JobResult("busy", "lock_busy")
    assert run_ids(store) == []
