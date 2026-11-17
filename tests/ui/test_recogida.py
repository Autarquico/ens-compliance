"""«Recoger ahora» de punta a punta: UI → petición → `execute` con un conector falso → UI.

Un bloque por escenario de docs/specs/ui-recogida.md más las decisiones D3, D5 y D7 del plan. La web
solo deja el fichero; lo ejecuta `collect_requests.execute`, como haría el planificador de `app`.
Fronteras reales: el reloj (`FakeClock`) y el conector (falso, en subproceso `spawn`).
"""

import json
from datetime import timedelta

import pytest
import yaml
from scheduling.collect_fakes import CANARY, FakeConnector
from scheduling.fakes import FakeClock

from ens_evidence.connectors.registry import ConnectorRegistry
from ens_evidence.models.enums import Role
from ens_evidence.scheduling.collect import Plugins
from ens_evidence.scheduling.collect_requests import (
    EXPIRES_AFTER,
    REQUESTS_PREFIX,
    ChecksOutcome,
    CollectRequestResult,
    ErrorCode,
    Outcome,
    execute,
    pending_keys,
    request_collect,
)
from ens_evidence.scheduling.config import CollectConfig
from ens_evidence.scope.approval import APPROVAL_KEY, approve_scope
from ens_evidence.ui.roles import ROLES_KEY, Grant, dump_roles
from ens_evidence.ui.views.conectores import CATALOGUE
from ens_evidence.workspace.settings import load_settings

from .conftest import NOW, Session, mint, snapshot_of, ui_client
from .test_conectores import (
    GITHUB,
    GITHUB_FILE,
    HOSTILE,
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


def ask_through_the_ui(web):
    response = web.post("/conectores/github/recoger", {})
    assert response.status_code == 303
    return response


# --- Scenario: petición atendida ---------------------------------------------------------------


def test_la_pagina_muestra_la_peticion_pendiente_sin_boton(web, configured, audit):
    assert BUTTON in github_block(web)

    response = ask_through_the_ui(web)

    assert response.headers["location"] == "/conectores?pedida=github"
    assert configured.list(REQUESTS_PREFIX) == [FIRST]
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


def test_el_estado_en_curso_se_muestra_mientras_esta_reclamada(web, configured):
    ask_through_the_ui(web)
    configured.write_text(CLAIM, "{}\n")

    block = github_block(web)

    assert "En curso" in block and BUTTON not in block


def test_una_peticion_hecha_permite_pedir_otra(web, configured, settings, plugins, fake):
    ask_through_the_ui(web)
    run(configured, settings, plugins)

    again = web.post("/conectores/github/recoger", {})

    assert again.status_code == 303
    assert f"{REQUESTS_PREFIX}/github/000002.json" in configured.list(REQUESTS_PREFIX)


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
    assert "ya hay una petición" in response.text.lower()
    assert configured.list(REQUESTS_PREFIX) == [FIRST]
    assert audit_lines(audit, "saved") == []


# --- Scenario: scheduler parado ----------------------------------------------------------------


def test_una_peticion_sin_atender_pasadas_24_horas_aparece_caducada_y_se_puede_pedir_otra(
    web, configured, settings, plugins, fake
):
    request_collect(configured, "github", email=EMAIL, now=NOW - EXPIRES_AFTER - timedelta(hours=1))

    block = github_block(web)

    assert "Caducada" in block
    assert "Comprueba que el servicio de recogida está en marcha" in plain(block)
    assert BUTTON in block
    assert pending_keys(configured, NOW) == []
    assert run(configured, settings, plugins) is None
    assert configured.list(REQUESTS_PREFIX) == [FIRST]
    assert web.post("/conectores/github/recoger", {}).status_code == 303
    assert f"{REQUESTS_PREFIX}/github/000002.json" in configured.list(REQUESTS_PREFIX)


# --- Scenario: solo lectura --------------------------------------------------------------------


def test_un_post_de_un_lector_da_403_auditado_y_no_escribe_nada(reader, configured, audit):
    before = snapshot_of(configured)

    response = reader.post("/conectores/github/recoger", {})

    assert response.status_code == 403
    assert snapshot_of(configured) == before
    assert configured.list(REQUESTS_PREFIX) == []
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
    assert example_store.list(REQUESTS_PREFIX) == []
    assert audit_lines(audit, "saved") == []


@pytest.mark.parametrize("name", sorted(CATALOGUE))
def test_el_boton_solo_aparece_en_los_conectores_configurados(name, web):
    block = block_of(web.get("/conectores").text, name)

    assert (BUTTON in block) is (name == "github")


# --- D3: reclamada pasadas 24 h ----------------------------------------------------------------


def test_una_reclamada_pasadas_24_horas_se_interrumpe_y_el_boton_vuelve(
    web, configured, settings, plugins, fake
):
    request_collect(configured, "github", email=EMAIL, now=NOW - timedelta(hours=30))
    configured.write_text(CLAIM, "{}\n")
    assert "En curso" in github_block(web)

    result = run(configured, settings, plugins, at=NOW)

    assert (result.outcome, result.code) == (Outcome.ERROR, ErrorCode.INTERRUPTED)
    assert not (fake.state / "calls").exists()
    block = plain(github_block(web))
    assert "No se pudo hacer: la ejecución se interrumpió" in block
    assert BUTTON in github_block(web)


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
