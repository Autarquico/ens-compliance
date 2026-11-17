"""El reclamo y la admisión de una petición son una sola transición por conector (`claim_lock`).

Garantía: en ningún instante hay dos peticiones vivas del mismo conector, ni siquiera si el
planificador lee la petición justo antes de las 24 h y la UI admite la siguiente a las 24 h, o si
el proceso muere a mitad. Los puntos de pausa son deterministas (relojes que esperan un evento).
"""

import multiprocessing
import threading
from datetime import UTC, datetime, timedelta

import pytest
from checks.test_engine import NOW

from ens_evidence.scheduling import collect_requests as module
from ens_evidence.scheduling.collect_requests import (
    EXPIRES_AFTER,
    REQUESTS_PREFIX,
    ErrorCode,
    RejectReason,
    RequestRejected,
    State,
    claim_lock,
    execute,
    list_requests,
    pending_keys,
    request_collect,
)
from ens_evidence.scheduling.config import CollectConfig
from ens_evidence.workspace import LocalStore
from scheduling.fakes import FakeClock

EMAIL = "ana@clinica.example"
ASKED_AT = NOW + timedelta(days=1)
LIMIT = ASKED_AT + EXPIRES_AFTER
FIRST = f"{REQUESTS_PREFIX}/github/000001.json"
SECOND = f"{REQUESTS_PREFIX}/github/000002.json"
_SPAWN = multiprocessing.get_context("spawn")


def ask(store, at):
    return request_collect(store, "github", email=EMAIL, now=at)


def alive(store, at) -> list[str]:
    return [r.key for r in list_requests(store, at) if r.state.alive]


def run(store, settings, plugins, clock, key=FIRST):
    return execute(store, settings, key, plugins=plugins, cfg=CollectConfig(), clock=clock)


class GatedClock(FakeClock):
    """La primera lectura avisa y espera: congela al planificador dentro de su reclamo."""

    def __init__(self, start):
        super().__init__(start)
        self.entered = threading.Event()
        self.release = threading.Event()

    def now(self):
        if not self.entered.is_set():
            self.entered.set()
            assert self.release.wait(10)
        return super().now()


def in_thread(target):
    outcome: dict[str, object] = {}

    def body():
        try:
            outcome["value"] = target()
        except BaseException as error:  # noqa: BLE001 - se devuelve al test
            outcome["error"] = error

    thread = threading.Thread(target=body)
    thread.start()
    return thread, outcome


# --- (a) el planificador se pausa antes de reclamar ----------------------------------------------


def test_un_planificador_que_se_reanuda_pasadas_24_horas_no_resucita_la_caducada(
    store, settings, plugins
):
    first = ask(store, ASKED_AT)
    assert pending_keys(store, LIMIT - timedelta(seconds=1)) == [first.key]  # lo lee y se pausa

    second = ask(store, LIMIT)  # la UI admite la siguiente: la primera ya caducó
    assert alive(store, LIMIT) == [second.key]

    assert run(store, settings, plugins, FakeClock(LIMIT)) is None  # se reanuda
    assert not store.exists(f"{REQUESTS_PREFIX}/github/000001.started.json")
    [old, new] = list_requests(store, LIMIT)
    assert (old.state, new.state) == (State.EXPIRED, State.PENDING)
    assert alive(store, LIMIT) == [SECOND]


def test_la_ui_espera_al_reclamo_en_curso_y_despues_ve_la_peticion_en_curso(
    store, settings, plugins, monkeypatch
):
    ask(store, ASKED_AT)
    monkeypatch.setattr(
        module, "_carry_out", lambda *a: module._failed(ErrorCode.NOT_CONFIGURED, LIMIT)
    )
    clock = GatedClock(LIMIT - timedelta(seconds=1))
    scheduler, ran = in_thread(lambda: run(store, settings, plugins, clock))
    assert clock.entered.wait(10)  # el planificador tiene el lock y aún no ha creado `.started`

    ui, asked = in_thread(lambda: ask(store, LIMIT))
    ui.join(0.3)
    assert ui.is_alive()  # la UI espera: no ve una petición caducada que está a punto de reclamarse
    assert store.list(f"{REQUESTS_PREFIX}/github/") == [FIRST]  # la siguiente no se ha creado

    clock.release.set()
    scheduler.join(10)
    ui.join(10)

    assert "error" not in ran
    assert isinstance(asked["error"], RequestRejected)
    assert asked["error"].reason is RejectReason.BUSY
    assert store.exists(f"{REQUESTS_PREFIX}/github/000001.started.json")
    assert alive(store, LIMIT) == []  # terminó (resultado escrito) y nunca hubo dos vivas


# --- (b) el planificador muere ------------------------------------------------------------------


def test_si_el_planificador_muere_tras_reclamar_solo_queda_una_viva(
    store, settings, plugins, monkeypatch
):
    ask(store, ASKED_AT)

    def die(*args):
        raise KeyboardInterrupt

    monkeypatch.setattr(module, "_carry_out", die)
    with pytest.raises(KeyboardInterrupt):
        run(store, settings, plugins, FakeClock(LIMIT - timedelta(seconds=1)))

    assert alive(store, LIMIT) == [FIRST]  # D3: reclamada antes de las 24 h sigue viva
    with pytest.raises(RequestRejected) as rejected:
        ask(store, LIMIT)
    assert rejected.value.reason is RejectReason.BUSY
    assert [item.key for item in list_requests(store, LIMIT)] == [FIRST]


def test_si_el_planificador_muere_al_decidir_en_el_limite_no_quedan_dos_vivas(
    store, settings, plugins
):
    ask(store, ASKED_AT)

    class Dying(FakeClock):
        def now(self):
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run(store, settings, plugins, Dying(LIMIT))

    assert not store.exists(f"{REQUESTS_PREFIX}/github/000001.started.json")
    assert alive(store, LIMIT) == []  # caducada, sin reclamar
    second = ask(store, LIMIT)  # y el lock no quedó tomado por el muerto
    assert alive(store, LIMIT) == [second.key]


# --- (d) el plazo se confirma tras publicar el reclamo -------------------------------------------


class CrossesTheLimitOnPublish(FakeClock):
    """La segunda lectura (tras crear `.started`) ya está en el límite de las 24 h."""

    def __init__(self, start):
        super().__init__(start)
        self.reads = 0

    def now(self):
        self.reads += 1
        if self.reads > 1:
            self.current = LIMIT
        return super().now()


def test_si_cruza_las_24_horas_durante_la_publicacion_no_recoge_y_cierra_caducada(
    store, settings, plugins, monkeypatch
):
    ask(store, ASKED_AT)
    monkeypatch.setattr(module, "_carry_out", lambda *a: pytest.fail("recogió tras caducar"))

    result = run(store, settings, plugins, CrossesTheLimitOnPublish(LIMIT - timedelta(seconds=1)))

    assert result is not None and result.code is ErrorCode.EXPIRED
    assert alive(store, LIMIT) == []  # nadie vio un RUNNING provisional: se cerró bajo el lock
    assert ask(store, LIMIT).key == SECOND


def test_un_reclamo_sin_confirmar_de_una_caducada_no_cuenta_como_viva(store):
    ask(store, ASKED_AT)
    store.create_bytes(f"{REQUESTS_PREFIX}/github/000001.started.json", b"{}\n")  # murió aquí

    assert alive(store, LIMIT - timedelta(seconds=1)) == [
        FIRST
    ]  # aún en plazo: la cierra la recuperación
    assert alive(store, LIMIT) == []
    second = ask(store, LIMIT)  # se admite otra
    assert alive(store, LIMIT) == [second.key]
    assert [item.state for item in list_requests(store, LIMIT)] == [State.EXPIRED, State.PENDING]


def test_un_reclamo_confirmado_antes_de_las_24_horas_sigue_viva_despues(
    store, settings, plugins, monkeypatch
):
    ask(store, ASKED_AT)
    monkeypatch.setattr(module, "_carry_out", lambda *a: (_ for _ in ()).throw(KeyboardInterrupt))
    with pytest.raises(KeyboardInterrupt):
        run(store, settings, plugins, FakeClock(LIMIT - timedelta(seconds=1)))

    assert alive(store, LIMIT + timedelta(hours=5)) == [FIRST]
    with pytest.raises(RequestRejected):
        ask(store, LIMIT + timedelta(hours=5))


# --- el fichero del lock ------------------------------------------------------------------------


def test_el_fichero_del_lock_no_es_una_peticion(store):
    ask(store, ASKED_AT)

    assert f"{REQUESTS_PREFIX}/github.lock" in store.list(REQUESTS_PREFIX)
    assert [r.key for r in list_requests(store, ASKED_AT)] == [FIRST]
    assert pending_keys(store, ASKED_AT) == [FIRST]


# --- (c) dos procesos reales --------------------------------------------------------------------


def _hold_the_lock(root: str, held, done) -> None:
    store = LocalStore(root)
    with claim_lock(store, "github"):
        held.set()
        assert done.wait(10)


def test_el_lock_excluye_a_otro_proceso(store):
    held, done = _SPAWN.Event(), _SPAWN.Event()
    child = _SPAWN.Process(target=_hold_the_lock, args=(str(store._root), held, done))
    child.start()
    try:
        assert held.wait(30)
        waiter, _ = in_thread(lambda: ask(store, ASKED_AT))
        waiter.join(0.5)
        assert waiter.is_alive()  # la admisión espera al proceso que tiene el lock
        assert not store.list(f"{REQUESTS_PREFIX}/github/")
    finally:
        done.set()
        child.join(30)
    waiter.join(10)
    assert child.exitcode == 0
    assert store.list(f"{REQUESTS_PREFIX}/github/") == [FIRST]


# --- el fin del calendario no desborda las fechas ------------------------------------------------

FAR = datetime(9999, 12, 31, tzinfo=UTC)


def test_un_marcador_confirmado_en_el_ultimo_dia_representable_no_desborda(
    store, settings, plugins, monkeypatch
):
    ask(store, FAR)
    monkeypatch.setattr(module, "_carry_out", lambda *a: (_ for _ in ()).throw(KeyboardInterrupt))
    with pytest.raises(KeyboardInterrupt):
        run(store, settings, plugins, FakeClock(FAR + timedelta(hours=12)))

    at = FAR + timedelta(hours=13)
    assert [item.state for item in list_requests(store, at)] == [State.RUNNING]
    assert pending_keys(store, at) == [FIRST]


def test_una_peticion_en_el_ultimo_segundo_representable_tiene_estado_fijo(store):
    last = datetime(9999, 12, 31, 23, 59, 59, tzinfo=UTC)
    ask(store, last)

    assert [item.state for item in list_requests(store, last)] == [State.PENDING]
    assert pending_keys(store, last) == [FIRST]
