import os
from datetime import datetime, timedelta
from importlib.metadata import EntryPoint

import pytest
from checks.test_engine import NOW, collect, write_config
from checks.test_expected import COLLECTED
from typer.testing import CliRunner

from ens_evidence.checks.engine import run_all
from ens_evidence.cli import app
from ens_evidence.connectors.registry import ENTRY_POINT_GROUP
from ens_evidence.evidence.manifests import list_manifests
from ens_evidence.models.enums import CheckStatus, CollectionStatus
from ens_evidence.scheduling import collect as collect_module
from ens_evidence.scheduling.collect import (
    GENERATED_BY,
    Phase,
    Plugins,
    collect_all,
    collect_one,
    load_plugins,
)
from ens_evidence.scheduling.config import CollectConfig
from ens_evidence.workspace.cycle_lock import CycleLock
from scheduling.collect_fakes import CANARY, LEAK, LOCK_INODE_ENV

ATTEMPT_AT = COLLECTED + timedelta(days=1)
NO_RETRIES = CollectConfig(retries=0)
FAST_TIMEOUT_MINUTES = 0.03


class FakeClock:
    def __init__(self, start: datetime = ATTEMPT_AT) -> None:
        self.current = start
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return self.current

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.current += timedelta(seconds=seconds)


def run_one(store, settings, plugins, **options):
    options.setdefault("cfg", NO_RETRIES)
    options.setdefault("clock", FakeClock())
    return collect_one(store, settings, plugins, "github", ATTEMPT_AT, **options)


def evidence_count(store):
    return len(list_manifests(store).manifests)


def github_statuses(store):
    outcome = next(o for o in run_all(store, NOW) if o.connector == "github")
    return {r.status for r in outcome.run.results}


def everything_stored(store):
    return "\n".join(
        store.read_bytes(key).decode("utf-8", "replace") for key in store.list("") if key
    )


# --- fallos por fase: nunca reaparece el PASS anterior -----------------------------------------


@pytest.mark.parametrize(
    ("mode", "phase", "error"),
    [
        ("plugin_load", Phase.PLUGIN_LOAD, "plugin_load_crashed:RuntimeError"),
        ("prepare", Phase.PREPARE, "prepare_crashed:RuntimeError"),
        ("collect", Phase.COLLECT, "collect_crashed:RuntimeError"),
        ("die", Phase.COLLECT, "collect_crashed:ChildExit"),
        ("secret", Phase.RESULT_REJECTED, "secret_detected:leak.detail"),
    ],
)
def test_un_fallo_deja_un_error_sintetizado_y_el_pass_anterior_no_reaparece(
    store,
    settings,
    plugins,
    fake,
    mode,
    phase,
    error,
):
    collect(store)
    assert CheckStatus.PASS in github_statuses(store)
    fake(mode)

    outcome = run_one(store, settings, plugins)

    result = outcome.result
    assert result.collection_status is CollectionStatus.ERROR
    assert (result.collector, result.asset_id, result.collected_at) == (
        "github",
        "gh-taller",
        ATTEMPT_AT,
    )
    assert result.data == {"error": error, "generated_by": GENERATED_BY, "phase": phase.value}
    assert [a.phase for a in outcome.attempts] == [phase]
    statuses = github_statuses(store)
    assert CheckStatus.PASS not in statuses
    assert statuses


def test_un_timeout_mata_al_collector_y_deja_phase_timeout(
    store,
    settings,
    plugins,
    fake,
):
    collect(store)
    fake("hang")

    outcome = run_one(store, settings, plugins, timeout_minutes=FAST_TIMEOUT_MINUTES)

    assert outcome.result.data["phase"] == "timeout"
    assert outcome.result.data["error"] == "timeout"
    assert_dead(fake.state)
    assert CheckStatus.PASS not in github_statuses(store)


def test_un_collector_que_ignora_sigterm_se_mata_con_kill(
    store,
    settings,
    plugins,
    fake,
    monkeypatch,
):
    monkeypatch.setattr(collect_module, "TERMINATE_GRACE_SECONDS", 0.2)
    fake("hang_ignoring_term")

    outcome = run_one(store, settings, plugins, timeout_minutes=FAST_TIMEOUT_MINUTES)

    assert outcome.result.data["phase"] == "timeout"
    assert_dead(fake.state)


class Stopped(BaseException):
    """Se pidió parar (lo que lanza el reloj del scheduler al recibir SIGTERM)."""


class StoppingClock(FakeClock):
    """Pide parar en cuanto el collector ya está colgado (ha escrito su pid)."""

    def __init__(self, hung_marker) -> None:
        super().__init__()
        self._marker = hung_marker

    def sleep(self, seconds: float) -> None:
        if self._marker.exists():
            raise Stopped


def test_parar_corta_una_recogida_aislada_en_curso_y_mata_al_hijo(
    store, settings, plugins, fake, monkeypatch
):
    monkeypatch.setattr(collect_module, "STOP_POLL_SECONDS", 0.2)
    fake("hang")

    with pytest.raises(Stopped):
        run_one(
            store,
            settings,
            plugins,
            clock=StoppingClock(fake.state / "pid"),
            timeout_minutes=5,
        )

    assert_dead(fake.state)
    assert evidence_count(store) == 0


def test_error_type_no_deja_pasar_rutas_ni_valores():
    assert collect_module.error_type("collect_crashed:KeyError") == "KeyError"
    assert collect_module.error_type("identity_mismatch:asset_id") == "identity_mismatch"
    assert collect_module.error_type("secret_detected:leak.detail") == "secret_detected"
    assert collect_module.error_type("timeout") == "timeout"


def assert_dead(state):
    pid = int((state / "pid").read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_el_subproceso_no_hereda_el_descriptor_del_lock(
    store,
    settings,
    plugins,
    fake,
    monkeypatch,
):
    fake("lock_fd")
    with CycleLock.for_settings(settings) as lock:
        monkeypatch.setenv(LOCK_INODE_ENV, str(os.fstat(lock._fd).st_ino))

        outcome = run_one(store, settings, plugins)

    assert outcome.result.collection_status is CollectionStatus.SUCCESS
    assert outcome.result.data == {"inherited_lock": False}


# --- identidad del resultado -------------------------------------------------------------------


@pytest.mark.parametrize("field", ["source", "collector", "asset_id"])
@pytest.mark.parametrize("isolated", [False, True])
def test_un_resultado_con_identidad_ajena_no_se_importa_y_el_pass_anterior_no_reaparece(
    store, settings, plugins, fake, field, isolated
):
    collect(store)
    assert CheckStatus.PASS in github_statuses(store)
    fake(f"foreign_{field}")

    outcome = run_one(store, settings, plugins, isolated=isolated)

    result = outcome.result
    assert result.collection_status is CollectionStatus.ERROR
    assert (result.source, result.collector, result.asset_id) == ("github", "github", "gh-taller")
    assert result.data == {
        "error": f"identity_mismatch:{field}",
        "generated_by": GENERATED_BY,
        "phase": Phase.RESULT_REJECTED.value,
    }
    assert [a.phase for a in outcome.attempts] == [Phase.RESULT_REJECTED]
    assert LEAK[:8] not in everything_stored(store)
    statuses = github_statuses(store)
    assert statuses
    assert CheckStatus.PASS not in statuses


# --- frontera de secretos ----------------------------------------------------------------------


@pytest.mark.parametrize(("mode", "path"), [("secret", "leak.detail"), ("secret_error", "error")])
def test_un_secreto_en_el_resultado_no_deja_rastro_en_la_evidencia(
    store,
    settings,
    plugins,
    fake,
    mode,
    path,
):
    fake(mode)

    outcome = run_one(store, settings, plugins, isolated=False)

    assert outcome.result.data["error"] == f"secret_detected:{path}"
    assert outcome.result.data["phase"] == "result_rejected"
    assert LEAK not in everything_stored(store)
    assert evidence_count(store) == 1


def test_un_nombre_de_clave_con_secret_no_basta_para_rechazar_el_resultado(
    store, settings, plugins, fake
):
    fake("ok")
    result = collect_module.CollectionResult(
        source="github",
        collector="github",
        collected_at=ATTEMPT_AT,
        asset_id="gh-taller",
        data={"secret_scanning": "enabled", "token_kind": "classic"},
        collection_status=CollectionStatus.SUCCESS,
    )

    assert collect_module._leaked_paths(result) == []


@pytest.mark.parametrize("isolated", [False, True])
@pytest.mark.parametrize(
    ("mode", "path"),
    [
        ("secret_key", "<redacted>"),
        ("secret_schema_version", "schema_version"),
        ("secret_tuple", "items[0]"),
    ],
)
def test_la_frontera_cubre_claves_schema_version_y_tuplas(
    store, settings, plugins, fake, mode, path, isolated
):
    fake(mode)

    outcome = run_one(store, settings, plugins, isolated=isolated)

    assert outcome.result.data["phase"] == "result_rejected"
    assert outcome.result.data["error"] == f"secret_detected:{path}"
    assert LEAK not in everything_stored(store)


@pytest.mark.parametrize("isolated", [False, True])
def test_el_rechazo_de_prepare_con_un_secreto_no_se_reproduce(
    store, settings, plugins, fake, isolated
):
    fake("prepare_secret")

    outcome = run_one(store, settings, plugins, isolated=isolated, synthesize_rejection=False)

    assert outcome.result is None
    assert outcome.problem == collect_module.PREPARE_REDACTED
    assert LEAK not in repr(outcome) and evidence_count(store) == 0


@pytest.mark.parametrize("mode", ["prepare_invalid", "prepare_secret"])
@pytest.mark.parametrize("isolated", [False, True])
def test_el_rechazo_de_prepare_deja_un_error_sintetizado_sin_el_mensaje(
    store, settings, plugins, fake, mode, isolated
):
    collect(store)
    assert CheckStatus.PASS in github_statuses(store)
    fake(mode)

    outcome = run_one(store, settings, plugins, cfg=RETRIES, isolated=isolated)

    assert outcome.result.collection_status is CollectionStatus.ERROR
    assert outcome.result.data == {
        "error": "prepare_crashed:WorkspaceError",
        "generated_by": GENERATED_BY,
        "phase": "prepare",
    }
    assert [a.phase for a in outcome.attempts] == [Phase.PREPARE] * 3  # se reintenta
    assert CheckStatus.PASS not in github_statuses(store)
    assert LEAK not in everything_stored(store) and "inventario" not in everything_stored(store)


def test_de_las_excepciones_solo_se_guarda_el_tipo(store, settings, plugins, fake):
    fake("collect")

    run_one(store, settings, plugins)

    assert CANARY not in everything_stored(store)
    assert "ghp_" not in everything_stored(store)


# --- reintentos --------------------------------------------------------------------------------

RETRIES = CollectConfig(retries=2, retry_delay_minutes=[5, 15])


@pytest.mark.parametrize("mode", ["error", "collect"])
def test_reintenta_ante_error_o_excepcion_e_importa_solo_el_resultado_final(
    store,
    settings,
    plugins,
    fake,
    mode,
):
    fake(mode, fail_first=2)
    clock = FakeClock()

    outcome = run_one(store, settings, plugins, cfg=RETRIES, clock=clock, isolated=False)

    assert [a.status for a in outcome.attempts] == [
        CollectionStatus.ERROR,
        CollectionStatus.ERROR,
        CollectionStatus.SUCCESS,
    ]
    assert clock.sleeps == [300, 900]
    assert outcome.result.collection_status is CollectionStatus.SUCCESS
    assert evidence_count(store) == 1


def test_agotados_los_reintentos_se_importa_solo_el_ultimo_error(
    store,
    settings,
    plugins,
    fake,
):
    fake("collect")

    outcome = run_one(store, settings, plugins, cfg=RETRIES, isolated=False)

    assert len(outcome.attempts) == 3
    assert outcome.result.data["phase"] == "collect"
    assert evidence_count(store) == 1


@pytest.mark.parametrize(
    ("mode", "status"),
    [
        ("permission_denied", CollectionStatus.PERMISSION_DENIED),
        ("not_supported", CollectionStatus.NOT_SUPPORTED),
    ],
)
def test_permiso_denegado_y_no_soportado_no_se_reintentan(
    store,
    settings,
    plugins,
    fake,
    mode,
    status,
):
    fake(mode)
    clock = FakeClock()

    outcome = run_one(store, settings, plugins, cfg=RETRIES, clock=clock, isolated=False)

    assert len(outcome.attempts) == 1
    assert clock.sleeps == []
    assert outcome.result.collection_status is status


@pytest.mark.parametrize("mode", ["plugin_load", "secret"])
def test_carga_de_plugin_y_secretos_no_se_reintentan(
    store,
    settings,
    plugins,
    fake,
    mode,
):
    fake(mode)
    clock = FakeClock()

    outcome = run_one(store, settings, plugins, cfg=RETRIES, clock=clock, isolated=False)

    assert len(outcome.attempts) == 1
    assert clock.sleeps == []


# --- configuración inválida y aislamiento entre conectores --------------------------------------


def test_con_la_configuracion_invalida_no_se_sintetiza_nada(
    store,
    settings,
    plugins,
    fake,
):
    write_config(store, "github", {"asset_id": "gh-taller"})

    outcome = run_one(store, settings, plugins)

    assert outcome.result is None
    assert "organization" in outcome.problem
    assert evidence_count(store) == 0


@pytest.mark.parametrize("isolated", [True, False])
def test_en_modo_manual_si_el_conector_rechaza_la_configuracion_no_se_importa_nada(
    store, settings, plugins, fake, isolated
):
    fake("prepare_invalid")

    outcome = run_one(
        store, settings, plugins, cfg=RETRIES, isolated=isolated, synthesize_rejection=False
    )

    assert outcome.result is None
    assert outcome.problem == "asset_id: debe ser un activo del inventario"
    assert evidence_count(store) == 0


def test_un_plugin_roto_no_tumba_a_los_demas(store, settings, plugins, fake):
    write_config(
        store,
        "cloudflare",
        {"asset_id": "cf-taller", "account_id": "0123456789abcdef0123456789abcdef"},
    )
    fake("ok")
    broken = Plugins(plugins.registry, broken=("cloudflare",))

    outcomes = collect_all(store, settings, broken, ATTEMPT_AT, NO_RETRIES, FakeClock())

    assert [o.connector for o in outcomes] == ["cloudflare", "github"]
    cloudflare, github = outcomes
    assert cloudflare.result.data["phase"] == "plugin_load"
    assert cloudflare.result.asset_id == "cf-taller"
    assert github.result.collection_status is CollectionStatus.SUCCESS


def test_collect_all_solo_recorre_los_conectores_con_configuracion(
    store,
    settings,
    plugins,
    fake,
):
    fake("ok")

    outcomes = collect_all(store, settings, plugins, ATTEMPT_AT, NO_RETRIES, FakeClock())

    assert [o.connector for o in outcomes] == ["github"]


def test_load_plugins_anota_el_entry_point_roto_y_carga_los_demas():
    good = EntryPoint("github", "scheduling.collect_fakes:FakeConnector", ENTRY_POINT_GROUP)
    broken = EntryPoint("zz-roto", "no_existe_xyz:Nada", ENTRY_POINT_GROUP)

    loaded = load_plugins([broken, good])

    assert loaded.registry.names() == ["github"]
    assert loaded.broken == ("zz-roto",)
    assert loaded.names() == ["github", "zz-roto"]


# --- CLI y lock --------------------------------------------------------------------------------


@pytest.fixture
def cli(settings_path, plugins, fake, monkeypatch):
    monkeypatch.setattr("ens_evidence.cli.clock", lambda: ATTEMPT_AT)
    monkeypatch.setattr("ens_evidence.cli.connector_registry", lambda: plugins.registry)
    monkeypatch.setattr("ens_evidence.cli.load_plugins", lambda: plugins)

    def run(*args: str):
        return CliRunner().invoke(app, ["--settings", str(settings_path), *args])

    return run


def test_collect_all_recoge_los_configurados_y_sale_con_0(cli, store, fake):
    fake("ok")

    result = cli("collect", "--all")

    assert result.exit_code == 0, result.output
    assert "github: Evidencia registrada:" in result.output
    assert evidence_count(store) == 1


def test_collect_all_con_un_fallo_sale_con_1_y_lo_registra(cli, store, fake):
    fake("collect")

    result = cli("collect", "--all")

    assert result.exit_code == 1
    assert "collect_crashed:RuntimeError" in result.output
    assert CANARY not in result.output


def test_collect_de_un_conector_usa_collect_one(cli, store, fake):
    fake("prepare")

    result = cli("collect", "github")

    assert result.exit_code == 1
    assert "prepare_crashed:RuntimeError" in result.output
    assert evidence_count(store) == 1


@pytest.mark.parametrize("args", [("collect", "--all"), ("collect", "github"), ("check", "--all")])
def test_con_el_ciclo_en_curso_sale_con_2_sin_escribir(cli, store, settings, args, fake):
    fake("ok")
    with CycleLock.for_settings(settings):
        result = cli(*args)

    assert result.exit_code == 2
    assert "ciclo en curso" in result.output
    assert evidence_count(store) == 0
    assert not store.list("evidence/checks")
    with CycleLock.for_settings(settings):  # el comando no se quedó con el lock
        pass


def test_collect_sin_conector_ni_all_falla_con_2(cli):
    result = cli("collect")

    assert result.exit_code == 2
    assert "--all" in result.output
