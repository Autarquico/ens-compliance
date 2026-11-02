"""`run_cycle`: etapas persistidas, reanudación, reloj real y copia que no rompe el ciclo."""

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from backup.conftest import PASSWORD

from ens_evidence.backup import PASSWORD_VAR, list_runs
from ens_evidence.backup.records import BackupRun, dump, run_key
from ens_evidence.checks.source import StoredCheckResults
from ens_evidence.reports.model import ConnectorAttempts, DailyReport, MonthlyReport, Unavailable
from ens_evidence.reports.store import (
    cycle_finished,
    load_json_strict,
    pairs,
    run_ids,
    stages_of,
    write_stage,
)
from ens_evidence.scheduling import cycle as cycle_module
from ens_evidence.scheduling.config import ScheduleConfig
from ens_evidence.scheduling.cycle import run_cycle
from ens_evidence.workspace import LocalStore, WorkspaceError

DEFAULT_CONFIG = ScheduleConfig()
DUE_AT = datetime(2026, 11, 1, 5, 0, tzinfo=UTC)  # 06:00 en Madrid
PERIOD = "2026-11-01"
STARTS = DUE_AT + timedelta(minutes=2)
STAGES = ["started", "collected", "checked", "reported", "backed_up"]


class Crash(BaseException):
    """Una caída del proceso: no la captura ningún `except Exception`."""


class TickClock:
    """Cada lectura avanza un segundo (las claves de los informes llevan microsegundos)."""

    def __init__(self, start: datetime = STARTS) -> None:
        self.current = start
        self.issued: list[datetime] = []

    def now(self) -> datetime:
        instant = self.current
        self.issued.append(instant)
        self.current += timedelta(seconds=1)
        return instant

    def sleep(self, seconds: float) -> None:
        self.current += timedelta(seconds=seconds)


class Hooks:
    """Cuenta las llamadas a cada etapa y puede tirar el proceso justo al entrar en una."""

    def __init__(self) -> None:
        self.calls: dict[str, list[tuple]] = {}
        self.crash_at: str | None = None

    def count(self, name: str) -> int:
        return len(self.calls.get(name, []))


@pytest.fixture
def hooks(monkeypatch):
    hooks = Hooks()
    for name in ("collect_all", "run_all", "build_daily", "run_configured_backup"):
        original = getattr(cycle_module, name)

        def wrapper(*args, _name=name, _original=original, **kwargs):
            hooks.calls.setdefault(_name, []).append(args)
            if hooks.crash_at == _name:
                raise Crash
            return _original(*args, **kwargs)

        monkeypatch.setattr(cycle_module, name, wrapper)
    return hooks


def cycle(
    store,
    settings,
    env,
    context,
    plugins,
    clock,
    *,
    cfg=DEFAULT_CONFIG,
    job="daily",
    period=PERIOD,
):
    return run_cycle(
        context,
        store,
        settings,
        env,
        job,
        period,
        DUE_AT,
        clock=clock,
        cfg=cfg,
        trigger="scheduled",
        plugins=plugins,
    )


def stage_names(store, run_id):
    return [m.stage for m in stages_of(store, run_id).stages]


def daily_reports(store, period=PERIOD):
    found = []
    for pair in pairs(store, "daily", period):
        found.append(DailyReport.model_validate(load_json_strict(store.read_bytes(pair.json_key))))
    return found


# --- el ciclo completo -------------------------------------------------------------------------


def test_un_ciclo_completo_deja_las_cinco_etapas_y_el_informe_con_su_ciclo(
    store, settings, env, context, plugins, fake, restic
):
    fake("ok")
    clock = TickClock()

    outcome = cycle(store, settings, env, context, plugins, clock)

    assert (outcome.completed, outcome.resumed, outcome.checks) == (True, False, "ok")
    assert stage_names(store, outcome.run_id) == STAGES
    assert cycle_finished(store, outcome.run_id)
    (pair,) = pairs(store, "daily", PERIOD)
    assert pair.published
    (report,) = daily_reports(store)
    info = report.cycle
    assert (info.run_id, info.period, info.due_at, info.trigger) == (
        outcome.run_id,
        PERIOD,
        DUE_AT,
        "scheduled",
    )
    assert info.started_at == STARTS
    assert info.data_cutoff in clock.issued and info.data_cutoff >= info.started_at
    assert [(a.connector, a.attempts) for a in info.connector_attempts] == [("github", 1)]
    reported = {m.stage: m for m in stages_of(store, outcome.run_id).stages}
    assert reported["reported"].reference == pair.json_key
    assert reported["backed_up"].reference.startswith("evidence/backups/")


def test_los_checks_corren_con_el_reloj_real_y_nunca_con_due_at(
    store, settings, env, context, plugins, fake, restic, hooks
):
    fake("ok")
    clock = TickClock()

    cycle(store, settings, env, context, plugins, clock)

    ((_, checked_at),) = hooks.calls["run_all"]
    assert checked_at in clock.issued and checked_at != DUE_AT
    runs = StoredCheckResults(reference_now=clock.current).runs(store, clock.current)
    assert runs and {r.finished_at for r in runs} == {checked_at}
    (report,) = daily_reports(store)
    assert report.cycle.data_cutoff > checked_at
    assert report.generated_at == report.cycle.data_cutoff


def test_un_ciclo_terminado_no_se_reanuda_y_el_siguiente_abre_otro_run(
    store, settings, env, context, plugins, fake, restic
):
    fake("ok")
    clock = TickClock()
    first = cycle(store, settings, env, context, plugins, clock)

    second = cycle(store, settings, env, context, plugins, clock)

    assert second.run_id != first.run_id and not second.resumed
    assert len(pairs(store, "daily", PERIOD)) == 2
    assert len(run_ids(store)) == 2


def test_el_trabajo_mensual_solo_genera_el_informe_mensual(
    store, settings, env, context, plugins, fake, restic, hooks
):
    fake("ok")

    outcome = cycle(
        store, settings, env, context, plugins, TickClock(), job="monthly", period="2026-11"
    )

    assert outcome.completed and len(outcome.reports) == 1
    assert stage_names(store, outcome.run_id) == ["started", "reported"]
    assert cycle_finished(store, outcome.run_id)
    assert (outcome.checks, outcome.backup_stage, outcome.collected) == (None, None, ())
    assert {name: hooks.count(name) for name in hooks.calls} == {}
    assert pairs(store, "daily", PERIOD) == []
    assert list_runs(store) == ([], [])
    monthly = MonthlyReport.model_validate(
        load_json_strict(store.read_bytes(pairs(store, "monthly", "2026-10")[0].json_key))
    )
    assert monthly.cycle.run_id == outcome.run_id and monthly.period == "2026-10"
    assert pairs(store, "monthly", "2026-10")[0].published


def test_un_ciclo_mensual_a_medias_se_reanuda_y_no_cuenta_como_terminado_hasta_reported(
    store, settings, env, context, plugins, fake, restic, monkeypatch
):
    fake("ok")
    monkeypatch.setattr(cycle_module, "build_monthly", lambda *a, **k: (_ for _ in ()).throw(Crash))
    with pytest.raises(Crash):
        cycle(store, settings, env, context, plugins, TickClock(), job="monthly", period="2026-11")
    (run_id,) = run_ids(store)
    assert not cycle_finished(store, run_id)
    assert cycle_module.finished_periods(store, STARTS) == set()
    monkeypatch.undo()

    outcome = cycle(
        store,
        settings,
        env,
        context,
        plugins,
        TickClock(STARTS + timedelta(minutes=5)),
        job="monthly",
        period="2026-11",
    )

    assert (outcome.run_id, outcome.resumed) == (run_id, True)
    assert cycle_module.finished_periods(store, STARTS) == {("monthly", "2026-11")}


def test_el_marcador_collected_guarda_los_intentos_y_la_reanudacion_los_conserva(
    store, settings, env, context, plugins, fake, restic, hooks
):
    fake("ok")
    hooks.crash_at = "run_all"
    with pytest.raises(Crash):
        cycle(store, settings, env, context, plugins, TickClock())
    (run_id,) = run_ids(store)
    collected = next(m for m in stages_of(store, run_id).stages if m.stage == "collected")
    assert [a.connector for a in collected.attempts] == ["github"]
    hooks.crash_at = None

    cycle(store, settings, env, context, plugins, TickClock(STARTS + timedelta(minutes=5)))

    (report,) = daily_reports(store)
    assert [(a.connector, a.attempts) for a in report.cycle.connector_attempts] == [("github", 1)]


def test_el_informe_reanudado_dice_que_se_reanudo_y_que_etapas_heredo(
    store, settings, env, context, plugins, fake, restic, hooks
):
    fake("ok")
    hooks.crash_at = "run_all"
    with pytest.raises(Crash):
        cycle(store, settings, env, context, plugins, TickClock())
    hooks.crash_at = None

    cycle(store, settings, env, context, plugins, TickClock(STARTS + timedelta(minutes=5)))

    (report,) = daily_reports(store)
    assert report.cycle.resumed is True
    assert [i.stage for i in report.cycle.inherited_stages] == ["collected"]


def test_un_ciclo_sin_caida_no_se_marca_como_reanudado(
    store, settings, env, context, plugins, fake, restic
):
    fake("ok")
    cycle(store, settings, env, context, plugins, TickClock())

    (report,) = daily_reports(store)
    assert (report.cycle.resumed, report.cycle.inherited_stages) == (False, [])


def forge_cycle(store, at, period=PERIOD, run_id="20261101T050000-forged"):
    """Marcadores de un ciclo que nunca corrió: intentos inventados y checks «hechos»."""
    forged = [ConnectorAttempts(connector="github", attempts=1, last_status="SUCCESS")]
    write_stage(store, run_id, "started", at=at, period=period)
    write_stage(store, run_id, "collected", at=at, period=period, attempts=forged)
    write_stage(store, run_id, "checked", at=at, period=period, checks="ok")
    return run_id


def test_unos_marcadores_del_futuro_no_se_reanudan_ni_se_saltan_etapas(
    store, settings, env, context, plugins, fake, restic, hooks
):
    fake("ok")
    forged = forge_cycle(store, STARTS + timedelta(days=3))

    outcome = cycle(store, settings, env, context, plugins, TickClock())

    assert (outcome.resumed, outcome.run_id != forged) == (False, True)
    assert (hooks.count("collect_all"), hooks.count("run_all")) == (1, 1)
    (report,) = daily_reports(store)
    assert [a.last_status.value for a in report.cycle.connector_attempts] == ["SUCCESS"]
    assert report.cycle.resumed is False
    assert isinstance(report.interrupted_cycles, Unavailable)
    assert forged in report.interrupted_cycles.reason


def test_unos_marcadores_de_un_periodo_futuro_no_se_reanudan(
    store, settings, env, context, plugins, fake, restic, hooks
):
    """Un ciclo del 1 de noviembre «empezado» el 31 de octubre no puede ser real."""
    fake("ok")
    forge_cycle(store, STARTS - timedelta(days=2))

    outcome = cycle(store, settings, env, context, plugins, TickClock())

    assert outcome.resumed is False and hooks.count("collect_all") == 1


def test_el_marcador_reported_lleva_la_huella_de_lo_publicado(
    store, settings, env, context, plugins, fake, restic
):
    fake("ok")
    outcome = cycle(store, settings, env, context, plugins, TickClock())

    (pair,) = pairs(store, "daily", PERIOD)
    marker = {m.stage: m for m in stages_of(store, outcome.run_id).stages}["reported"]
    assert marker.reference == pair.json_key
    assert marker.json_sha256 == hashlib.sha256(store.read_bytes(pair.json_key)).hexdigest()
    assert marker.html_sha256 == hashlib.sha256(store.read_bytes(pair.html_key)).hexdigest()


def test_las_ventanas_de_schedule_yaml_llegan_al_informe_diario(
    store, settings, env, context, plugins, fake, restic, monkeypatch
):
    fake("ok")
    seen = {}
    original = cycle_module.build_daily

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(cycle_module, "build_daily", spy)
    cfg = ScheduleConfig.model_validate({"expiry_warnings": {"technical_evidence": 3}})

    cycle(store, settings, env, context, plugins, TickClock(), cfg=cfg)

    assert seen["windows"]["technical_evidence"] == 3


def test_el_limite_de_tiempo_de_la_copia_llega_a_restic(
    store, settings, env, context, plugins, fake, restic, monkeypatch
):
    fake("ok")
    seen = {}
    original = cycle_module.run_configured_backup

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(cycle_module, "run_configured_backup", spy)
    cfg = ScheduleConfig.model_validate({"backup": {"timeout_minutes": 7}})

    cycle(store, settings, env, context, plugins, TickClock(), cfg=cfg)

    assert seen["timeout_seconds"] == 420


def test_finished_periods_deriva_lo_hecho_de_los_marcadores(
    store, settings, env, context, plugins, fake, restic
):
    fake("ok")
    cycle(store, settings, env, context, plugins, TickClock())

    assert cycle_module.finished_periods(store, STARTS) == {("daily", PERIOD)}
    assert cycle_module.finished_periods(store, STARTS + timedelta(days=41)) == set()


# --- caída entre etapas y reanudación ----------------------------------------------------------


class NoHtmlStore(LocalStore):
    """Frontera de almacenamiento: la escritura del HTML del informe se cae."""

    crash = False

    def create_bytes(self, key: str, data: bytes) -> None:
        if self.crash and key.startswith("evidence/reports/daily/") and key.endswith(".html"):
            raise Crash
        super().create_bytes(key, data)


# dónde cae, etapas que quedaron, y llamadas totales (incluida la que cayó) a cada etapa
CRASHES = [
    ("collect_all", ["started"], {"collect_all": 2, "run_all": 1, "build_daily": 1}),
    ("run_all", ["started", "collected"], {"collect_all": 1, "run_all": 2, "build_daily": 1}),
    (
        "build_daily",
        ["started", "collected", "checked"],
        {"collect_all": 1, "run_all": 1, "build_daily": 2},
    ),
    (
        "html",
        ["started", "collected", "checked"],
        {"collect_all": 1, "run_all": 1, "build_daily": 1},
    ),
    (
        "run_configured_backup",
        ["started", "collected", "checked", "reported"],
        {"collect_all": 1, "run_all": 1, "build_daily": 1, "run_configured_backup": 2},
    ),
]


@pytest.mark.parametrize(("where", "reached", "totals"), CRASHES, ids=[c[0] for c in CRASHES])
def test_tras_una_caida_se_reanudan_solo_las_etapas_pendientes_del_mismo_run(
    tmp_path, settings, env, context, plugins, fake, restic, hooks, where, reached, totals
):
    fake("ok")
    store = NoHtmlStore(tmp_path / "ws")
    clock = TickClock()
    hooks.crash_at = where
    store.crash = where == "html"
    with pytest.raises(Crash):
        cycle(store, settings, env, context, plugins, clock)
    (run_id,) = run_ids(store)
    assert stage_names(store, run_id) == reached
    before = {m.stage: m.at for m in stages_of(store, run_id).stages}
    hooks.crash_at, store.crash = None, False

    clock.current += timedelta(hours=20)  # cruza la medianoche: el period sigue siendo el original
    outcome = cycle(store, settings, env, context, plugins, clock)

    assert (outcome.completed, outcome.resumed, outcome.run_id) == (True, True, run_id)
    assert run_ids(store) == [run_id]
    assert stage_names(store, run_id) == STAGES
    after = {m.stage: m.at for m in stages_of(store, run_id).stages}
    assert all(after[stage] == at for stage, at in before.items())
    assert {name: hooks.count(name) for name in totals} == totals
    (pair,) = pairs(store, "daily", PERIOD)
    assert pair.published
    assert pairs(store, "daily", "2026-11-02") == []


def test_json_sin_html_se_completa_desde_el_json_guardado_sin_tocarlo(
    tmp_path, settings, env, context, plugins, fake, restic, hooks
):
    fake("ok")
    store = NoHtmlStore(tmp_path / "ws")
    store.crash = True
    with pytest.raises(Crash):
        cycle(store, settings, env, context, plugins, TickClock())
    (half,) = pairs(store, "daily", PERIOD)
    assert half.json_key and half.html_key is None
    json_before = store.read_bytes(half.json_key)
    store.crash = False

    cycle(store, settings, env, context, plugins, TickClock(STARTS + timedelta(minutes=5)))

    (pair,) = pairs(store, "daily", PERIOD)
    assert pair.published and pair.json_key == half.json_key
    assert store.read_bytes(pair.json_key) == json_before
    assert PERIOD in store.read_text(pair.html_key)


# --- el informe y los checks ---------------------------------------------------------------------


def test_un_fallo_del_informe_falla_el_ciclo_sin_filtrar_el_mensaje_y_el_reintento_lo_reanuda(
    store, settings, env, context, plugins, fake, restic, monkeypatch
):
    fake("ok")
    clock = TickClock()
    with monkeypatch.context() as patch:

        def broken(*_args, **_kwargs):
            raise RuntimeError("token-secreto-123")

        patch.setattr(cycle_module, "build_daily", broken)
        failed = cycle(store, settings, env, context, plugins, clock)

    assert (failed.status, failed.failure) == ("failed", "report_failed:RuntimeError")
    assert "token-secreto-123" not in repr(failed)
    assert stage_names(store, failed.run_id) == ["started", "collected", "checked"]
    assert pairs(store, "daily", PERIOD) == []

    retry = cycle(store, settings, env, context, plugins, clock)

    assert (retry.completed, retry.resumed, retry.run_id) == (True, True, failed.run_id)
    assert stage_names(store, failed.run_id) == STAGES


def test_alcance_sin_aprobar_deja_los_checks_omitidos_y_el_ciclo_sigue(
    tmp_path, settings, env, context, plugins, restic
):
    empty = LocalStore(tmp_path / "vacio")

    outcome = cycle(empty, settings, env, context, plugins, TickClock())

    assert (outcome.completed, outcome.checks) == (True, "skipped")
    markers = {m.stage: m for m in stages_of(empty, outcome.run_id).stages}
    assert markers["checked"].checks == "skipped_scope"
    assert stage_names(empty, outcome.run_id) == STAGES


def test_un_error_del_workspace_en_los_checks_es_un_error_de_etapa_y_el_ciclo_sigue(
    store, settings, env, context, plugins, fake, restic, monkeypatch
):
    fake("ok")

    def broken(*_args, **_kwargs):
        raise WorkspaceError("detalle interno")

    monkeypatch.setattr(cycle_module, "run_all", broken)

    outcome = cycle(store, settings, env, context, plugins, TickClock())

    assert (outcome.completed, outcome.checks) == (True, "error")
    markers = {m.stage: m for m in stages_of(store, outcome.run_id).stages}
    assert markers["checked"].checks == "error"
    assert "detalle interno" not in repr(markers)


# --- la copia ------------------------------------------------------------------------------------


def only_report(store):
    (pair,) = pairs(store, "daily", PERIOD)
    return pair, store.read_bytes(pair.json_key), store.read_bytes(pair.html_key)


def test_copia_activada_con_exito_queda_como_backed_up_con_su_registro(
    store, settings, env, context, plugins, fake, restic
):
    fake("ok")

    outcome = cycle(store, settings, env, context, plugins, TickClock())

    assert outcome.backup_stage == "backed_up"
    (record,) = list_runs(store)[0]
    markers = {m.stage: m for m in stages_of(store, outcome.run_id).stages}
    assert (record.status, markers["backed_up"].reference) == ("completed", run_key(record.run_id))


def test_copia_sin_credenciales_queda_en_backup_failed_y_el_ciclo_termina(
    store, settings, env, context, plugins, fake, restic
):
    fake("ok")
    del env[PASSWORD_VAR]

    outcome = cycle(store, settings, env, context, plugins, TickClock())

    assert (outcome.completed, outcome.backup_stage) == (True, "backup_failed")
    assert cycle_finished(store, outcome.run_id)
    markers = {m.stage: m for m in stages_of(store, outcome.run_id).stages}
    assert markers["backup_failed"].backup_code == "invalid_credentials"
    (record,) = list_runs(store)[0]
    assert (record.status, record.error) == ("failed", "invalid_credentials")
    assert PASSWORD not in repr(markers)


def test_un_fallo_de_restic_no_rompe_el_ciclo_y_el_informe_ya_escrito_no_cambia(
    store, settings, env, context, plugins, fake, scripted_restic
):
    fake("ok")
    scripted_restic.reply("backup", err=f"boom {PASSWORD}", code=1)

    outcome = cycle(store, settings, env, context, plugins, TickClock())

    assert (outcome.completed, outcome.backup_stage) == (True, "backup_failed")
    markers = {m.stage: m for m in stages_of(store, outcome.run_id).stages}
    (record,) = list_runs(store)[0]
    assert markers["backup_failed"].reference == run_key(record.run_id)
    assert markers["backup_failed"].backup_code == "restic_failed"
    pair, json_bytes, _ = only_report(store)
    assert PASSWORD not in json_bytes.decode()


def test_copia_desactivada_queda_como_backup_skipped_y_no_llama_a_restic(
    store, settings, env, context, plugins, fake, restic
):
    fake("ok")
    cfg = ScheduleConfig.model_validate({"backup": {"enabled": False}})

    outcome = cycle(store, settings, env, context, plugins, TickClock(), cfg=cfg)

    assert (outcome.completed, outcome.backup_stage) == (True, "backup_skipped")
    assert restic.calls() == [] and list_runs(store)[0] == []
    markers = {m.stage: m for m in stages_of(store, outcome.run_id).stages}
    assert markers["backup_skipped"].backup_code == "disabled"


def test_un_backup_started_sin_finalizar_se_informa_y_no_se_lanza_otro(
    store, settings, env, context, plugins, fake, restic
):
    fake("ok")
    orphan = BackupRun(run_id="BK-huerfano", status="started", started_at=STARTS, repository="r")
    store.create_bytes(run_key(orphan.run_id), dump(orphan))

    second = cycle(store, settings, env, context, plugins, TickClock(STARTS + timedelta(hours=1)))

    assert second.backup_stage == "backup_failed"
    markers = {m.stage: m for m in stages_of(store, second.run_id).stages}
    assert markers["backup_failed"].backup_code == "previous_run_unfinished"
