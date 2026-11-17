"""«Recoger ahora» de punta a punta: UI → petición → `execute` con un conector falso → UI.

Un bloque por escenario de docs/specs/ui-recogida.md más las decisiones D3, D5 y D7 del plan. La web
solo deja el fichero; lo ejecuta `collect_requests.execute`, como haría el planificador de `app`.
Fronteras reales: el reloj (`FakeClock`) y el conector (falso, en subproceso `spawn`).
"""

import json
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
import yaml
from scheduling.collect_fakes import CANARY, FakeConnector
from scheduling.fakes import FakeClock
from scheduling.test_scheduler import CFG

from ens_evidence.connectors.registry import ConnectorRegistry
from ens_evidence.models.enums import CollectionStatus, Role
from ens_evidence.scheduling import collect_requests
from ens_evidence.scheduling.collect import Plugins
from ens_evidence.scheduling.collect_requests import (
    EXPIRES_AFTER,
    REQUESTS_PREFIX,
    ChecksOutcome,
    CollectRequestResult,
    ErrorCode,
    Outcome,
    State,
    execute,
    list_requests,
    pending_keys,
    request_collect,
)
from ens_evidence.scheduling.config import CollectConfig
from ens_evidence.scheduling.scheduler import CycleExecutor, Scheduler
from ens_evidence.scope.approval import APPROVAL_KEY, approve_scope
from ens_evidence.ui.roles import ROLES_KEY, Grant, dump_roles
from ens_evidence.ui.views.conectores import CATALOGUE
from ens_evidence.ui.views.conectores_peticion import CHECKS_LABELS
from ens_evidence.workspace.cycle_lock import CycleLock
from ens_evidence.workspace.settings import load_settings

from .conftest import NOW, Session, mint, snapshot_of, ui_client
from .test_conectores import (
    GITHUB,
    GITHUB_FILE,
    HOSTILE,
    REPOSITORY,
    audit_lines,
    block_of,
)
from .test_conectores import audit as audit  # noqa: PLC0414
from .test_inventory import FORBIDDEN, favorable, text

EMAIL = "ana@example.com"
OTHER = "luis@example.com"
FIRST = f"{REQUESTS_PREFIX}/github/000001.json"
CLAIM = f"{REQUESTS_PREFIX}/github/000001.started.json"
RESULT = f"{REQUESTS_PREFIX}/github/000001.result.json"
BUTTON = "Recoger ahora</button>"
RAN_AT = NOW + timedelta(seconds=30)
TOKEN = "ghp_" + "d" * 30


@pytest.fixture
def configured(example_store):
    """Alcance aprobado y GitHub configurado: lo que hace falta para recoger y evaluar."""
    approve_scope(example_store, "Ana", Role.SYSTEM_OWNER, NOW)
    example_store.write_text(GITHUB_FILE, yaml.safe_dump(GITHUB))
    return example_store


@pytest.fixture
def web(config, configured, key, token) -> Session:
    return Session(ui_client(config, configured, key), token)


@pytest.fixture
def other_web(config, configured, key) -> Session:
    """Un segundo editor; `web` (Ana) sigue siendo editor."""
    configured.write_text(
        ROLES_KEY,
        dump_roles({EMAIL: Grant(True, frozenset(Role)), OTHER: Grant(True, frozenset(Role))}),
    )
    return Session(ui_client(config, configured, key), mint(key, email=OTHER))


@pytest.fixture
def reader(config, configured, key, token) -> Session:
    configured.write_text(ROLES_KEY, dump_roles({EMAIL: Grant(False, frozenset())}))
    return Session(ui_client(config, configured, key), token)


@pytest.fixture
def settings(configured, tmp_path):
    path = tmp_path / "settings.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "workspace": {"backend": "local", "local": {"path": str(configured._root)}},
                "backup": {"restic_repository": str(tmp_path / "repo")},
            }
        )
    )
    return load_settings(path, allow_inside_product=True)


@pytest.fixture
def plugins():
    registry = ConnectorRegistry()
    registry.register(FakeConnector)
    return Plugins(registry)


@pytest.fixture
def fake(monkeypatch, tmp_path):
    state = tmp_path / "fake-state"
    state.mkdir()
    monkeypatch.setenv("ENS_FAKE_STATE", str(state))
    monkeypatch.setenv("ENS_STATE_DIR", str(tmp_path / "state"))

    def mode(name: str) -> None:
        monkeypatch.setenv("ENS_FAKE_MODE", name)

    mode.state = state
    return mode


def requests_listed(store) -> list[str]:
    """Las claves bajo `collect-requests` sin el fichero del lock por conector."""
    return [key for key in store.list(REQUESTS_PREFIX) if not key.endswith(".lock")]


def run(store, settings, plugins, at=RAN_AT, key=FIRST):
    return execute(store, settings, key, plugins=plugins, cfg=CollectConfig(), clock=FakeClock(at))


def stored_result(store, key=RESULT) -> CollectRequestResult:
    return CollectRequestResult.model_validate_json(store.read_bytes(key))


def github_block(web) -> str:
    return block_of(web.get("/conectores").text, "github")


def plain(html: str) -> str:
    return " ".join(text(html).split())


def request_card(block: str) -> str:
    """Solo la tarjeta de la petición: «Última recogida» tiene su propia proyección y tono."""
    start = block.index("Recoger ahora</h3>")
    return block[start:]


def checks_label_count(web, result) -> int:
    return plain(request_card(github_block(web))).count(CHECKS_LABELS[result.checks])


def ask_through_the_ui(web):
    response = web.post("/conectores/github/recoger", {})
    assert response.status_code == 303
    return response


# --- Scenario: petición atendida ---------------------------------------------------------------


def test_la_pagina_muestra_la_peticion_pendiente_sin_boton(web, configured, audit):
    assert BUTTON in github_block(web)

    response = ask_through_the_ui(web)

    assert response.headers["location"] == "/conectores?pedida=github"
    assert requests_listed(configured) == [FIRST]
    block = github_block(web)
    assert "Pendiente" in block and BUTTON not in block
    assert "Petición de recogida de GitHub creada" in web.get("/conectores?pedida=github").text


def test_el_scheduler_recoge_evalua_y_la_pagina_muestra_la_nueva_recogida(
    web, configured, settings, plugins, fake
):
    ask_through_the_ui(web)

    result = run(configured, settings, plugins)

    assert (result.outcome, result.checks) == (Outcome.DONE, ChecksOutcome.RECORDED)
    assert stored_result(configured) == result
    assert configured.exists(CLAIM)
    assert pending_keys(configured, RAN_AT) == []
    block = plain(github_block(web))
    assert "Terminada" in block and "comprobaciones registradas" in block
    assert result.evidence_id in block and "Última recogida" in block
    assert BUTTON in github_block(web)
    assert request_card(github_block(web)).count("tone-ok") == 0
    assert checks_label_count(web, result) == 1


def test_el_flujo_completo_la_web_pide_y_un_tick_real_del_scheduler_la_atiende(
    web, configured, settings, plugins, fake, tmp_path
):
    state = tmp_path / "lock-state"
    clock = FakeClock(RAN_AT)
    executor = CycleExecutor(
        configured,
        settings,
        {},
        clock,
        threading.Event(),
        plugins=plugins,
        lock_factory=lambda _settings, _env: CycleLock("ws", state),
    )
    scheduler = Scheduler(
        clock,
        executor,
        lambda: CFG,
        lambda: {("daily", "2026-11-08")},
        threading.Event(),
        log=lambda line: None,
        collect_requests=lambda now: pending_keys(configured, now),
    )
    ask_through_the_ui(web)
    assert "Pendiente" in github_block(web)

    [(job, outcome)] = scheduler.tick()

    assert (job.kind, job.period, outcome.status) == ("collect", FIRST, "completed")
    result = stored_result(configured)
    assert (result.outcome, result.checks) == (Outcome.DONE, ChecksOutcome.RECORDED)
    block = plain(github_block(web))
    assert "Terminada" in block and "comprobaciones registradas" in block
    assert result.evidence_id in block and "Última recogida" in block
    assert BUTTON in github_block(web)
    assert scheduler.tick() == []


def test_un_fallo_de_los_checks_tras_importar_conserva_la_evidencia_y_la_pagina_lo_dice(
    web, configured, settings, plugins, fake, monkeypatch
):
    def exploding_checks(*args, **kwargs):
        raise RuntimeError(CANARY)

    monkeypatch.setattr(collect_requests, "run_one", exploding_checks)
    ask_through_the_ui(web)

    result = run(configured, settings, plugins)

    assert result.collection_status is CollectionStatus.SUCCESS
    assert (result.outcome, result.checks, result.check_key) == (
        Outcome.DONE,
        ChecksOutcome.ERROR,
        None,
    )
    stored = stored_result(configured)
    assert stored == result and stored.evidence_id is not None
    assert any(stored.evidence_id in key for key in configured.list("evidence"))
    assert not configured.list("evidence/checks")
    card = request_card(github_block(web))
    assert "Terminada; comprobaciones no registradas" in card
    assert "fallaron" in card and stored.evidence_id in card
    assert "tone-warn" in card and "tone-ok" not in card
    assert CANARY not in web.get("/conectores").text
    assert CANARY not in configured.read_bytes(RESULT).decode()


def test_el_estado_en_curso_se_muestra_mientras_esta_reclamada(web, configured):
    ask_through_the_ui(web)
    configured.write_text(CLAIM, "{}\n")

    block = github_block(web)

    assert "En curso" in block and BUTTON not in block


def test_con_la_recogida_en_pausa_la_pagina_dice_en_curso_y_otro_post_se_rechaza_sin_esperar(
    web, configured, settings, plugins, fake, monkeypatch
):
    inside, resume = threading.Event(), threading.Event()
    real = collect_requests._carry_out

    def paused(*args):
        inside.set()
        assert resume.wait(30)
        return real(*args)

    monkeypatch.setattr(collect_requests, "_carry_out", paused)
    ask_through_the_ui(web)
    outcome: dict[str, object] = {}

    def body():
        try:
            outcome["result"] = run(configured, settings, plugins)
        except BaseException as error:  # noqa: BLE001 - se devuelve al test
            outcome["error"] = error

    worker = threading.Thread(target=body)
    worker.start()
    try:
        assert inside.wait(30)  # el lock por conector ya está libre
        assert "En curso" in github_block(web) and BUTTON not in github_block(web)
        started = time.monotonic()
        refused = web.post("/conectores/github/recoger", {})
        assert refused.status_code == 422
        assert time.monotonic() - started < 5  # no espera a la recogida
        assert requests_listed(configured) == [FIRST, CLAIM]
    finally:
        resume.set()
        worker.join(60)

    assert "error" not in outcome
    assert stored_result(configured).outcome is Outcome.DONE
    assert BUTTON in github_block(web)


def test_una_peticion_hecha_permite_pedir_otra(web, configured, settings, plugins, fake):
    ask_through_the_ui(web)
    run(configured, settings, plugins)

    again = web.post("/conectores/github/recoger", {})

    assert again.status_code == 303
    assert f"{REQUESTS_PREFIX}/github/000002.json" in requests_listed(configured)


def test_la_accion_se_audita_como_recoger_con_la_clave_de_la_peticion(web, audit):
    ask_through_the_ui(web)

    [line] = audit_lines(audit, "saved")

    assert f"identity={EMAIL} step=conectores action=recoger asset=github" in line
    assert f"keys={FIRST}" in line


# --- Scenario: una sola petición viva ----------------------------------------------------------


def test_otro_editor_no_crea_una_segunda_peticion_y_la_pagina_lo_dice(
    web, other_web, configured, audit
):
    ask_through_the_ui(web)
    audit.clear()

    response = other_web.post("/conectores/github/recoger", {})

    assert response.status_code == 422
    assert "ya hay una petición de recogida de github" not in response.text
    assert "Ya hay una petición de recogida de GitHub sin terminar." in response.text
    assert requests_listed(configured) == [FIRST]
    assert audit_lines(audit, "saved") == []


# --- Scenario: scheduler parado ----------------------------------------------------------------


def test_una_peticion_sin_atender_pasadas_24_horas_aparece_caducada_y_se_puede_pedir_otra(
    web, configured, settings, plugins, fake
):
    request_collect(configured, "github", email=EMAIL, now=NOW - EXPIRES_AFTER - timedelta(hours=1))

    block = github_block(web)

    assert "Caducada" in block
    assert "Comprueba que el servicio de recogida está en marcha" in plain(block)
    assert BUTTON in block and "ídela de nuevo" in plain(block)
    assert pending_keys(configured, NOW) == []
    assert run(configured, settings, plugins) is None
    assert requests_listed(configured) == [FIRST]
    assert web.post("/conectores/github/recoger", {}).status_code == 303
    assert f"{REQUESTS_PREFIX}/github/000002.json" in requests_listed(configured)


@pytest.mark.parametrize(
    "marker",
    [
        "null",
        "[]",
        "{nope",
        "{}",
        '{"confirmed_at": null}',
        '{"confirmed_at": "2020-01-01T00:00:00"}',
    ],
)
def test_un_marcador_started_danado_pasado_el_plazo_no_rompe_la_pagina_y_se_puede_pedir_otra(
    web, configured, marker
):
    request_collect(configured, "github", email=EMAIL, now=NOW - EXPIRES_AFTER - timedelta(hours=1))
    configured.write_text(CLAIM, marker)

    block = github_block(web)

    assert "Caducada" in block and BUTTON in block
    assert web.post("/conectores/github/recoger", {}).status_code == 303
    assert f"{REQUESTS_PREFIX}/github/000002.json" in requests_listed(configured)


def test_caducada_con_la_configuracion_invalida_no_invita_a_pedir_otra(web, configured):
    request_collect(configured, "github", email=EMAIL, now=NOW - EXPIRES_AFTER - timedelta(hours=1))
    configured.write_text(GITHUB_FILE, "asset_id: [sin cerrar")

    for page in (web.get("/conectores").text, web.get("/conectores/github").text):
        shown = plain(page)
        assert "Caducada" in shown and "Corrige la configuración antes de pedir otra." in shown
        assert "ídela de nuevo" not in shown and BUTTON not in page


# --- Scenario: solo lectura --------------------------------------------------------------------


def test_un_post_de_un_lector_da_403_auditado_y_no_escribe_nada(reader, configured, audit):
    before = snapshot_of(configured)

    response = reader.post("/conectores/github/recoger", {})

    assert response.status_code == 403
    assert snapshot_of(configured) == before
    assert requests_listed(configured) == []
    [line] = audit_lines(audit, "denied")
    assert "path=/conectores/github/recoger" in line and "reason=no_edit" in line


def test_un_lector_no_ve_el_boton(reader):
    assert BUTTON not in reader.get("/conectores").text
    assert BUTTON not in reader.get("/conectores/github").text


def test_un_conector_fuera_del_catalogo_da_403_para_un_editor_y_no_escribe(web, configured, audit):
    before = snapshot_of(configured)

    response = web.post("/conectores/desconocido/recoger", {})

    assert response.status_code == 403
    assert snapshot_of(configured) == before
    assert "reason=unknown_operation" in audit_lines(audit, "denied")[0]


# --- Conector sin configurar -------------------------------------------------------------------


def test_un_conector_sin_configurar_no_ofrece_el_boton(config, example_store, key, token):
    web = Session(ui_client(config, example_store, key), token)

    block = block_of(web.get("/conectores").text, "github")

    assert "Sin configurar" in block and BUTTON not in block


def test_un_post_directo_a_un_conector_sin_configurar_da_422_y_no_escribe(
    config, example_store, key, token, audit
):
    web = Session(ui_client(config, example_store, key), token)
    before = snapshot_of(example_store)

    response = web.post("/conectores/github/recoger", {})

    assert response.status_code == 422
    assert snapshot_of(example_store) == before
    assert requests_listed(example_store) == []
    assert audit_lines(audit, "saved") == []


@pytest.mark.parametrize("name", sorted(CATALOGUE))
def test_el_boton_solo_aparece_en_los_conectores_configurados(name, web):
    block = block_of(web.get("/conectores").text, name)

    assert (BUTTON in block) is (name == "github")


@pytest.mark.parametrize("name", ["github", "backups", "cloudflare"])
def test_la_lista_y_la_ficha_coinciden_en_el_boton(name, web, configured):
    configured.write_text(
        "connectors/backups.yaml",
        yaml.safe_dump({"repositories": [{**REPOSITORY, "id": "caja", "asset_id": "bk-diario"}]}),
    )

    in_list = BUTTON in block_of(web.get("/conectores").text, name)
    in_detail = BUTTON in web.get(f"/conectores/{name}").text

    assert in_list is in_detail is (name != "cloudflare")


# --- D3: reclamada pasadas 24 h ----------------------------------------------------------------


def test_una_reclamada_pasadas_24_horas_se_interrumpe_y_el_boton_vuelve(
    web, configured, settings, plugins, fake
):
    request_collect(configured, "github", email=EMAIL, now=NOW - timedelta(hours=30))
    confirmed_at = (NOW - timedelta(hours=29)).isoformat()
    configured.write_text(CLAIM, f'{{"confirmed_at": "{confirmed_at}"}}\n')
    assert "En curso" in github_block(web)

    result = run(configured, settings, plugins, at=NOW)

    assert (result.outcome, result.code) == (Outcome.ERROR, ErrorCode.INTERRUPTED)
    assert not (fake.state / "calls").exists()
    block = plain(github_block(web))
    assert "No se pudo hacer: la ejecución se interrumpió" in block
    assert BUTTON in github_block(web)


def test_leida_pendiente_y_reclamada_a_las_24_horas_no_se_reclama_y_queda_caducada(
    web, configured, settings, plugins, fake
):
    request_collect(
        configured, "github", email=EMAIL, now=NOW - EXPIRES_AFTER + timedelta(seconds=1)
    )
    assert "Pendiente" in github_block(web)
    clock = FakeClock(NOW + timedelta(seconds=1))

    result = execute(configured, settings, FIRST, plugins=plugins, cfg=CollectConfig(), clock=clock)

    assert result is None
    assert not configured.exists(CLAIM) and not configured.exists(RESULT)
    assert not (fake.state / "calls").exists()
    [old] = list_requests(configured, clock.current)
    assert old.state is State.EXPIRED and pending_keys(configured, clock.current) == []


# --- D5: identificadores hostiles --------------------------------------------------------------


@pytest.mark.parametrize(
    "fields",
    [
        {"evidence_id": HOSTILE},
        {"evidence_id": "../../etc/passwd"},
        {"evidence_id": f"EV-20261108-abcdef12 {TOKEN}"},
        {"check_key": HOSTILE},
        {"check_key": "evidence/checks/restic/000001-20261030T120000000000Z-0123456789ab.json"},
        {"evidence_id": HOSTILE, "check_key": "../../x"},
    ],
    ids=lambda f: "+".join(f),
)
def test_un_identificador_hostil_en_el_resultado_nunca_se_muestra_en_bruto(web, configured, fields):
    ask_through_the_ui(web)
    configured.write_text(
        RESULT,
        json.dumps(
            {
                "format": "ens-collect-request-result/1",
                "outcome": "DONE",
                "at": NOW.isoformat(),
                "attempts": 1,
                "checks": "RECORDED",
                "collection_status": "SUCCESS",
            }
            | fields
        ),
    )

    page = web.get("/conectores")

    assert page.status_code == 200
    block = github_block(web)
    assert "No se pudo hacer: la petición o su resultado no se pueden leer" in plain(block)
    for value in fields.values():
        assert value not in page.text
        assert "&lt;script&gt;" not in page.text
    assert "passwd" not in page.text and TOKEN not in page.text
    assert BUTTON in block


# --- D7: «Terminada» nunca es favorable --------------------------------------------------------


def test_terminada_con_error_de_recogida_no_es_favorable(web, configured, settings, plugins, fake):
    fake("collect")
    ask_through_the_ui(web)

    result = run(configured, settings, plugins)

    assert result.outcome is Outcome.DONE
    block = request_card(github_block(web))
    assert "Terminada con error de recogida" in block
    assert "tone-bad" in block and "tone-ok" not in block
    assert not favorable(block)
    assert CANARY not in web.get("/conectores").text
    assert checks_label_count(web, result) == 1


def test_terminada_con_las_comprobaciones_sin_registrar_no_es_favorable(
    web, configured, settings, plugins, fake
):
    (configured._root / APPROVAL_KEY).unlink()
    ask_through_the_ui(web)

    result = run(configured, settings, plugins)

    assert result.checks is ChecksOutcome.SKIPPED_SCOPE
    block = request_card(github_block(web))
    assert "Terminada; comprobaciones no registradas" in block
    assert "el alcance no está aprobado" in block
    assert "tone-warn" in block and "tone-ok" not in block
    assert not favorable(block)
    assert checks_label_count(web, result) == 1


def test_con_la_configuracion_estropeada_dice_no_se_pudo_hacer_sin_valores_del_fichero(
    web, configured, settings, plugins, fake
):
    ask_through_the_ui(web)
    configured.write_text(GITHUB_FILE, "asset_id: [sin cerrar")

    result = run(configured, settings, plugins)

    assert result.code is ErrorCode.CONFIG_INVALID
    assert "No se pudo hacer: la configuración no es válida" in plain(github_block(web))


# --- Copy --------------------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["ok", "collect"])
def test_el_texto_de_la_peticion_no_usa_palabras_prohibidas_en_ningun_estado(
    web, configured, settings, plugins, fake, mode
):
    fake(mode)
    assert not FORBIDDEN.search(text(github_block(web)))
    ask_through_the_ui(web)
    assert not FORBIDDEN.search(text(github_block(web)))
    run(configured, settings, plugins)

    page = web.get("/conectores").text

    assert not FORBIDDEN.search(text(page)), FORBIDDEN.search(text(page))


def test_un_fallo_al_importar_deja_la_peticion_sin_evidencia_y_nunca_favorable(
    web, configured, settings, plugins, fake, monkeypatch
):
    """Entre el artefacto y el manifiesto: el artefacto queda huérfano (sin manifiesto, sin
    recogida visible) y la petición se cierra `COLLECT_CRASHED` sin `evidence_id`."""
    create = configured.create_bytes

    def fail_on_manifest(key, data):
        if key.startswith("evidence/manifests/"):
            raise OSError(CANARY)
        create(key, data)

    monkeypatch.setattr(configured, "create_bytes", fail_on_manifest)
    ask_through_the_ui(web)

    result = run(configured, settings, plugins)

    assert (result.outcome, result.code) == (Outcome.ERROR, ErrorCode.COLLECT_CRASHED)
    assert result.evidence_id is None and result.collection_status is None
    assert stored_result(configured) == result
    assert not [k for k in configured.list("evidence/manifests") if "EV-" in k]
    assert len(configured.list("evidence/raw")) == 1  # el artefacto huérfano
    page = web.get("/conectores").text
    block = github_block(web)
    card = request_card(block).split("Resultados de las comprobaciones")[0]
    assert "tone-bad" in card and "tone-ok" not in card
    assert not favorable(block)
    assert "la recogida falló de forma inesperada" in plain(block)
    assert "Sin recogidas todavía" in plain(github_block(web))
    assert CANARY not in page and CANARY not in configured.read_bytes(RESULT).decode()


# --- el fin del calendario no desborda las fechas ------------------------------------------------


@pytest.mark.parametrize("confirmed", [True, False])
def test_peticiones_en_el_ultimo_dia_representable_no_rompen_la_pagina(web, configured, confirmed):
    far = datetime(9999, 12, 31, tzinfo=UTC)
    asked = request_collect(configured, "github", email="ana@clinica.example", now=far)
    if confirmed:
        marker = {"confirmed_at": (far + timedelta(hours=12)).isoformat()}
        configured.write_bytes(CLAIM, json.dumps(marker).encode())

    assert [item.state for item in list_requests(configured, far)] == [
        State.RUNNING if confirmed else State.PENDING
    ]
    assert pending_keys(configured, far) == [asked.key]
    assert web.get("/conectores").status_code == 200
