"""Inventario de punta a punta: la app real y las plantillas reales, sin sustituir nada.

Un escenario por bloque (docs/specs/ui-inventario.md) más la edición (K9). Todo lo que no
es una frontera (red, reloj) corre de verdad.
"""

import re
from datetime import timedelta
from html import unescape

import pytest
import yaml
from agent.protocol_support import (
    ENDPOINT_CONFIG,
    LAPTOP,
    LAPTOP_HOST,
    LAPTOP_UUID,
    SERVER,
)

from ens_evidence.agent.node_key import normalize_host_identifier
from ens_evidence.agent.results import contact_result, write_result
from ens_evidence.checks.model import StoredCheckRun
from ens_evidence.checks.store import record_run
from ens_evidence.models.contracts import CheckResult
from ens_evidence.models.enums import CheckStatus, Role
from ens_evidence.scope import ScopeState, approve_scope, scope_status
from ens_evidence.ui.roles import ROLES_KEY, Grant, dump_roles

from .conftest import NOW, Session, snapshot_of, ui_client

SHA = "a" * 64
FORBIDDEN = re.compile(r"cumple|garantiza|certific", re.IGNORECASE)
HOST_ID = normalize_host_identifier(LAPTOP_UUID)
PROVIDER = {
    "asset_type": "provider",
    "asset_id": "prov-x",
    "asset_service": "Correo",
    "asset_function": "Envío de correo",
    "asset_dependency": "Alta",
    "scope": "included",
}


@pytest.fixture
def web(config, example_store, key, token) -> Session:
    return Session(ui_client(config, example_store, key), token)


@pytest.fixture
def readonly(config, example_store, key, token) -> Session:
    example_store.write_text(ROLES_KEY, dump_roles({"ana@example.com": Grant(False, frozenset())}))
    return Session(ui_client(config, example_store, key), token)


@pytest.fixture
def audit(caplog):
    import logging

    caplog.set_level(logging.INFO, logger="ens_evidence.ui.audit")
    return caplog


def record(store, connector, results, *, ago=1, complete=True):
    finished = NOW - timedelta(hours=ago)
    record_run(
        store,
        StoredCheckRun(
            seq=1,
            connector=connector,
            engine_version="1",
            started_at=finished - timedelta(minutes=5),
            finished_at=finished,
            complete=complete,
            scope_fingerprint=SHA,
            config_sha256=SHA,
            results=results,
        ),
        NOW,
    )


def result(asset, status, check="HOST-001"):
    return CheckResult(
        check_id=check,
        asset_id=asset,
        status=status,
        evaluated_at=NOW,
        evidence_ids=["EV-1"],
        reason="r",
        reason_code=None if status is CheckStatus.PASS else "some_code",
    )


def row_of(html: str, asset_id: str) -> str:
    """La fila del listado de un activo (la primera tabla)."""
    start = html.index(f'<a href="/inventario/activos/{asset_id}">')
    return html[html.rindex("<tr>", 0, start) : html.index("</tr>", start)]


def text(html: str) -> str:
    return unescape(re.sub(r"<[^>]+>", " ", html))


def assets_of(store):
    return {a["id"]: a for a in yaml.safe_load(store.read_text("scope/assets.yaml"))["assets"]}


def system_of(store):
    return yaml.safe_load(store.read_text("scope/system.yaml"))


def approve(store):
    approve_scope(store, "Ana Ruiz", Role.SYSTEM_OWNER, NOW)


def favorable(html: str) -> bool:
    """Hay una insignia de tono favorable que dice «PASS» o «Sin fallos»."""
    return bool(re.search(r'tone-ok"[^>]*>.{0,400}?<span>(PASS|Sin fallos)', html, re.DOTALL))


def assert_no_write_controls(html: str) -> None:
    assert 'method="post"' not in html
    for marker in ("?editar=1", "?baja=1", "Añadir un activo", "Añadir al inventario"):
        assert marker not in html


# --- Scenario: listado con evidencia -----------------------------------------------------------


def test_el_primero_muestra_su_peor_estado_con_fecha_y_fuente_y_el_segundo_sin_evidencia(
    web, example_store
):
    record(
        example_store,
        "endpoint",
        [result(SERVER, CheckStatus.PASS), result(SERVER, CheckStatus.FAIL, "HOST-002")],
    )

    response = web.get("/inventario")

    assert response.status_code == 200
    with_evidence = text(row_of(response.text, SERVER))
    assert "FAIL" in with_evidence
    assert "2026-11-08 11:00 UTC" in with_evidence and "endpoint" in with_evidence
    without = row_of(response.text, LAPTOP)
    assert "sin evidencia" in without
    assert "tone-" not in without  # ni siquiera un tono neutro de estado


def test_ningun_activo_aparece_como_conforme_ni_con_tono_favorable_sin_evidencia(
    web, example_store
):
    record(example_store, "endpoint", [result(SERVER, CheckStatus.FAIL)])

    html = web.get("/inventario").text

    assert not FORBIDDEN.search(text(html))
    for asset in ("lap-personal", LAPTOP, "app-facturas"):
        row = row_of(html, asset)
        assert "sin evidencia" in row and "tone-ok" not in row


def test_un_pass_completo_se_pinta_como_resultado_no_como_conformidad(web, example_store):
    record(example_store, "endpoint", [result(SERVER, CheckStatus.PASS)])

    html = web.get("/inventario").text

    assert favorable(row_of(html, SERVER))  # control: el detector sí ve un tono favorable
    row = text(row_of(html, SERVER))
    assert "PASS" in row and "Último resultado de 1 check de endpoint" in row
    assert not FORBIDDEN.search(row)


def test_un_pass_con_historial_ilegible_no_lleva_tono_favorable(web, example_store):
    record(example_store, "endpoint", [result(SERVER, CheckStatus.PASS)])
    example_store.write_text("evidence/checks/github/roto.json", "no es json")

    html = web.get("/inventario").text

    row = row_of(html, SERVER)
    assert "PASS parcial" in row and "tone-ok" not in row and "Cobertura parcial" in row


def test_el_listado_dice_alcance_justificacion_y_despliegue(web):
    html = web.get("/inventario").text

    personal = text(row_of(html, "lap-personal"))
    assert "Equipo personal sin acceso a producción" in personal
    assert "Planificado" in text(row_of(html, "bk-externo"))


@pytest.mark.parametrize(
    ("query", "present", "absent"),
    [
        ("tipo=server", SERVER, LAPTOP),
        ("despliegue=planned", "bk-externo", SERVER),
        ("alcance=excluded", "lap-personal", LAPTOP),
        ("estado=FAIL", SERVER, LAPTOP),
        ("estado=sin-evidencia", LAPTOP, SERVER),
    ],
)
def test_los_filtros_dejan_solo_los_activos_que_coinciden(
    web, example_store, query, present, absent
):
    record(example_store, "endpoint", [result(SERVER, CheckStatus.FAIL)])

    html = web.get(f"/inventario?{query}").text

    assert f'<a href="/inventario/activos/{present}">' in html
    assert f'<a href="/inventario/activos/{absent}">' not in html
    assert "Quitar filtros" in html


def test_un_filtro_sin_coincidencias_lo_dice(web):
    html = web.get("/inventario?tipo=server&despliegue=planned").text

    assert "Ningún activo coincide con los filtros." in html


# --- Scenario: ficha del activo ----------------------------------------------------------------


def test_la_ficha_muestra_campos_servicio_y_resultados_por_conector_con_fuente_y_fecha(
    web, example_store
):
    record(example_store, "endpoint", [result("app-facturas", CheckStatus.PASS)])
    record(example_store, "github", [result("app-facturas", CheckStatus.FAIL, "GH-1")], ago=2)

    response = web.get("/inventario/activos/app-facturas")

    assert response.status_code == 200
    page = text(response.text)
    assert "facturas.taller-norte.example" in page and "taller-norte/facturas" in page
    assert "svc-facturacion" in page and "Facturación online" in page
    assert "dep-app-db" in page  # K9: dependencias en la ficha
    assert "endpoint" in page and "2026-11-08 11:00 UTC" in page
    assert "github" in page and "2026-11-08 10:00 UTC" in page
    assert "GH-1" in page and "EV-1" in page
    assert not FORBIDDEN.search(page)


def test_la_ficha_sin_resultados_dice_sin_evidencia(web):
    html = web.get("/inventario/activos/lap-personal").text

    assert "Sin evidencia" in html and "tone-ok" not in html
    assert "Ningún servicio ni dependencia usa este activo." in html


def test_la_ficha_de_un_pass_dice_sin_fallos_no_correcto(web, example_store):
    record(example_store, "endpoint", [result("app-facturas", CheckStatus.PASS)])

    page = text(web.get("/inventario/activos/app-facturas").text)

    assert "Sin fallos" in page and "Correcto" not in page and not FORBIDDEN.search(page)


def test_la_ficha_de_un_activo_inexistente_es_404(web):
    assert web.get("/inventario/activos/no-existe").status_code == 404


def test_la_ficha_de_un_equipo_inscrito_enlaza_el_panel_de_agentes(web, example_store):
    example_store.write_text("connectors/endpoint.yaml", yaml.safe_dump(ENDPOINT_CONFIG))

    assert f'href="/agentes/{LAPTOP}"' in web.get(f"/inventario/activos/{LAPTOP}").text


def test_la_ficha_con_un_id_que_pide_codificacion_url(web, example_store):
    web.post("/inventario/alta", PROVIDER | {"asset_id": "prov#1+ñ"})

    response = web.get("/inventario/activos/prov%231%2B%C3%B1")

    assert response.status_code == 200 and "prov#1+ñ" in response.text
    assert 'href="/inventario/activos/prov%231%2B%C3%B1"' in web.get("/inventario").text


# --- Scenario: alta que cambia el alcance ------------------------------------------------------


def test_la_ui_advierte_antes_de_guardar_el_activo_queda_y_la_aprobacion_se_revoca(
    web, example_store
):
    approve(example_store)

    form = web.get("/inventario/alta?tipo=provider")
    assert form.status_code == 200
    assert "Guardar revoca la aprobación del alcance" in form.text
    assert 'name="acepto_revocacion"' in form.text

    done = web.post("/inventario/alta", PROVIDER | {"acepto_revocacion": "1"})

    assert done.status_code == 303 and done.headers["location"].endswith("hecho=nuevo&revocada=1")
    assert "prov-x" in assets_of(example_store)
    assert "prov-x" in system_of(example_store)["included_assets"]
    assert scope_status(example_store).state is ScopeState.NOT_APPROVED
    landing = web.get(done.headers["location"]).text
    assert "Activo dado de alta." in landing and "se ha revocado" in landing


def test_sin_aprobacion_no_hay_aviso_de_revocacion(web):
    assert "Guardar revoca" not in web.get("/inventario/alta?tipo=provider").text


def test_sin_aceptar_la_revocacion_se_repinta_con_el_error_y_no_se_escribe(web, example_store):
    approve(example_store)
    before = snapshot_of(example_store)

    response = web.post("/inventario/alta", PROVIDER)

    assert response.status_code == 422
    assert 'value="prov-x"' in response.text  # conserva lo escrito
    assert "Guardar revoca la aprobación del alcance" in response.text
    assert snapshot_of(example_store) == before
    assert scope_status(example_store).state is ScopeState.APPROVED


def test_el_alta_queda_auditada_con_el_activo_y_sin_valores(web, audit):
    web.post("/inventario/alta", PROVIDER)

    line = next(r.message for r in audit.records if "action=nuevo" in r.message)
    assert (
        "outcome=saved identity=ana@example.com step=inventario action=nuevo asset=prov-x" in line
    )
    assert "Correo" not in line


def test_el_alta_de_un_portatil_pide_y_guarda_el_hostname(web, example_store):
    form = web.get("/inventario/alta?tipo=laptop&id=lap-nueva&hostname=lap-nueva.x")
    assert 'name="asset_hostname"' in form.text and 'value="lap-nueva.x"' in form.text

    data = {
        "asset_type": "laptop",
        "asset_id": "lap-nueva",
        "asset_hostname": "lap-nueva.x",
        "asset_os": "macOS 15",
        "asset_owner": "Luis",
        "asset_encrypted": "sí",
        "asset_usage": "Desarrollo",
        "scope": "included",
    }
    assert web.post("/inventario/alta", data).status_code == 303
    assert assets_of(example_store)["lap-nueva"]["hostname"] == "lap-nueva.x"


def test_un_alta_invalida_se_repinta_con_lo_escrito_y_no_escribe(web, example_store):
    before = snapshot_of(example_store)

    response = web.post("/inventario/alta", PROVIDER | {"asset_id": "con espacio"})

    assert response.status_code == 422
    assert 'aria-invalid="true"' in response.text
    assert "El identificador no puede llevar espacios." in response.text
    assert 'value="con espacio"' in response.text and 'value="Correo"' in response.text
    assert snapshot_of(example_store) == before


@pytest.mark.parametrize("changes", [{"asset_id": "a/b"}, {"scope": ""}, {"asset_function": ""}])
def test_otras_altas_invalidas_dan_422_y_no_escriben(web, example_store, changes):
    before = snapshot_of(example_store)

    assert web.post("/inventario/alta", PROVIDER | changes).status_code == 422
    assert snapshot_of(example_store) == before


def test_un_alta_excluida_sin_justificacion_se_rechaza(web, example_store):
    before = snapshot_of(example_store)

    response = web.post("/inventario/alta", PROVIDER | {"scope": "excluded"})

    assert response.status_code == 422
    assert snapshot_of(example_store) == before


# --- Scenario: baja de un activo referenciado --------------------------------------------------


def test_se_rechaza_explicando_que_lo_referencia_y_no_se_escribe_nada(web, example_store, audit):
    before = snapshot_of(example_store)
    page = web.get("/inventario/activos/db-facturas?baja=1")
    assert "No se puede dar de baja todavía." in page.text

    response = web.post("/inventario/activos/db-facturas/baja", {"confirmar": "db-facturas"})

    assert response.status_code == 422
    html = response.text
    assert "svc-facturacion" in html and "dep-app-db" in html
    assert snapshot_of(example_store) == before
    assert not [r for r in audit.records if "action=baja" in r.message]


def test_la_baja_de_un_activo_libre_pide_confirmar_y_lo_quita(web, example_store, audit):
    page = web.get("/inventario/activos/lap-personal?baja=1")
    assert 'name="confirmar"' in page.text
    before = snapshot_of(example_store)

    wrong = web.post("/inventario/activos/lap-personal/baja", {"confirmar": "otro"})
    assert wrong.status_code == 422 and snapshot_of(example_store) == before

    done = web.post("/inventario/activos/lap-personal/baja", {"confirmar": "lap-personal"})

    assert done.status_code == 303 and done.headers["location"] == "/inventario?hecho=baja"
    assert "lap-personal" not in assets_of(example_store)
    assert not [e for e in system_of(example_store)["exclusions"] if e["ref"] == "lap-personal"]
    line = next(r.message for r in audit.records if "action=baja" in r.message)
    assert "outcome=saved" in line and "asset=lap-personal" in line
    assert "Activo dado de baja." in web.get(done.headers["location"]).text


def test_la_baja_con_el_alcance_aprobado_exige_aceptar_la_revocacion(web, example_store):
    approve(example_store)
    before = snapshot_of(example_store)

    page = web.get("/inventario/activos/lap-personal?baja=1")
    assert 'name="acepto_revocacion"' in page.text
    refused = web.post("/inventario/activos/lap-personal/baja", {"confirmar": "lap-personal"})
    assert refused.status_code == 422 and snapshot_of(example_store) == before

    done = web.post(
        "/inventario/activos/lap-personal/baja",
        {"confirmar": "lap-personal", "acepto_revocacion": "1"},
    )
    assert done.status_code == 303
    assert scope_status(example_store).state is ScopeState.NOT_APPROVED


def test_la_baja_con_servicios_ilegibles_se_rechaza_sin_escribir(web, example_store):
    example_store.write_text("scope/services.yaml", "services: [")
    before = snapshot_of(example_store)

    page = web.get("/inventario/activos/lap-personal?baja=1")
    response = web.post("/inventario/activos/lap-personal/baja", {"confirmar": "lap-personal"})

    assert "No se pueden leer los servicios" in page.text
    assert response.status_code == 422 and snapshot_of(example_store) == before


# --- Scenario: sin permiso de edición ----------------------------------------------------------


def test_sin_edicion_se_ve_el_listado_y_las_fichas_sin_acciones_de_escritura(readonly):
    listing = readonly.get("/inventario")
    ficha = readonly.get(f"/inventario/activos/{LAPTOP}")

    assert listing.status_code == ficha.status_code == 200
    assert "Solo lectura" in listing.text and "Solo lectura" in ficha.text
    assert_no_write_controls(listing.text)
    assert_no_write_controls(ficha.text)


def test_sin_edicion_ni_el_formulario_de_edicion_ni_el_de_baja_se_pintan_activos(readonly):
    for query in ("editar=1", "baja=1"):
        html = readonly.get(f"/inventario/activos/lap-personal?{query}").text
        assert 'name="asset_os"' not in html and 'name="confirmar"' not in html


def test_sin_edicion_el_alta_se_ve_deshabilitada(readonly):
    html = readonly.get("/inventario/alta?tipo=provider").text

    assert '<fieldset class="gate" disabled>' in html


@pytest.mark.parametrize(
    ("path", "data"),
    [
        ("/inventario/alta", PROVIDER),
        (f"/inventario/activos/{LAPTOP}/editar", {"asset_os": "macOS 16"}),
        ("/inventario/activos/lap-personal/baja", {"confirmar": "lap-personal"}),
    ],
)
def test_un_post_sin_edicion_recibe_403_auditado_y_no_escribe(
    readonly, example_store, audit, path, data
):
    before = snapshot_of(example_store)

    response = readonly.post(path, data)

    assert response.status_code == 403
    assert snapshot_of(example_store) == before
    denied = [r.message for r in audit.records if "outcome=denied" in r.message]
    assert len(denied) == 1 and f"path={path}" in denied[0]
    assert not [r for r in audit.records if "outcome=saved" in r.message]


def test_con_edicion_la_ficha_ofrece_editar_y_dar_de_baja(web):
    html = web.get(f"/inventario/activos/{LAPTOP}").text

    assert f"/inventario/activos/{LAPTOP}?editar=1" in html
    assert f"/inventario/activos/{LAPTOP}?baja=1" in html
    assert "/inventario/alta" in web.get("/inventario").text


# --- Scenario: equipo que reporta y no está inventariado ---------------------------------------

LAPTOP_MISSING = """\
assets:
  - id: srv-app
    type: server
    deployment: deployed
    hostname: srv-app.taller-norte.example
    function: Aplicaciones
    os: Debian 12
    environment: producción
    location: Oficina
"""


def admit(store, *, ago=timedelta(hours=30), **fields):
    fields.setdefault("last_admission_at", (NOW - ago).isoformat())
    store.write_text("agents/registry.yaml", yaml.safe_dump({"agents": {LAPTOP: fields}}))


def contact(store, *, ago=timedelta(hours=1)):
    write_result(store, contact_result(LAPTOP, HOST_ID, "darwin", 1, now=NOW - ago), now=NOW)


@pytest.fixture
def agents(example_store):
    example_store.write_text("connectors/endpoint.yaml", yaml.safe_dump(ENDPOINT_CONFIG))
    return example_store


def test_un_equipo_que_reporta_y_no_esta_inventariado_aparece_con_el_alta_prellenada(web, agents):
    agents.write_text("scope/assets.yaml", LAPTOP_MISSING)
    admit(agents)
    contact(agents)

    response = web.get("/inventario")

    assert response.status_code == 200
    html = response.text
    assert "Reporta y no está inventariado" in html
    assert f"/inventario/alta?tipo=laptop&amp;id={LAPTOP}&amp;hostname={LAPTOP_HOST}" in html
    assert "Añadir al inventario" in html
    assert (
        web.get(f"/inventario/alta?tipo=laptop&id={LAPTOP}&hostname={LAPTOP_HOST}").status_code
        == 200
    )


def test_la_reconciliacion_no_bloquea_nada_y_no_escribe(web, agents):
    agents.write_text("scope/assets.yaml", LAPTOP_MISSING)
    admit(agents)
    contact(agents)
    before = snapshot_of(agents)

    web.get("/inventario")

    assert snapshot_of(agents) == before
    assert web.post("/inventario/alta", PROVIDER).status_code == 303


def test_un_equipo_admitido_sin_contacto_no_se_da_por_que_reporta(web, agents):
    admit(agents)

    html = web.get("/inventario").text

    assert "Admitido y sin contacto reciente" in html
    assert "Sin contacto desde el alta." in html
    assert "Reporta y no está inventariado" not in html


def test_un_desplegado_sin_alta_de_agente_y_un_hostname_distinto_se_marcan(web, agents):
    admit(agents)
    contact(agents)
    agents.write_text(
        "scope/assets.yaml",
        agents.read_text("scope/assets.yaml").replace(LAPTOP_HOST, "otro.taller-norte.example"),
    )

    html = web.get("/inventario").text

    assert "Desplegado en el inventario y sin alta en el registro de agentes" in html
    assert "Nombre distinto." in html and "otro.taller-norte.example" in html


def test_sin_edicion_la_reconciliacion_informa_pero_no_ofrece_el_alta(
    config, example_store, key, token
):
    example_store.write_text(ROLES_KEY, dump_roles({"ana@example.com": Grant(False, frozenset())}))
    example_store.write_text("connectors/endpoint.yaml", yaml.safe_dump(ENDPOINT_CONFIG))
    example_store.write_text("scope/assets.yaml", LAPTOP_MISSING)
    admit(example_store)
    contact(example_store)

    html = Session(ui_client(config, example_store, key), token).get("/inventario").text

    assert "Reporta y no está inventariado" in html
    assert "Añadir al inventario" not in html and "/inventario/alta" not in html


def test_sin_agentes_configurados_la_reconciliacion_lo_dice(web):
    html = web.get("/inventario").text

    assert "No hay agentes configurados" in html


# --- Scenario: inventario inválido -------------------------------------------------------------


@pytest.fixture
def broken(example_store):
    example_store.write_text("scope/assets.yaml", "assets:\n  - id: x\n    type: inventado\n")
    return example_store


def test_con_un_assets_invalido_se_muestran_los_errores_y_no_se_ofrece_escribir(web, broken):
    response = web.get("/inventario")

    assert response.status_code == 200
    assert "Hay ficheros del inventario con errores." in response.text
    assert "assets.yaml" in response.text
    assert "Solo lectura mientras el inventario no sea válido." in response.text
    assert_no_write_controls(response.text)


def test_con_un_assets_invalido_ningun_post_escribe(web, broken):
    before = snapshot_of(broken)

    alta = web.post("/inventario/alta", PROVIDER)
    editar = web.post(f"/inventario/activos/{LAPTOP}/editar", {"asset_os": "x"})
    baja = web.post(f"/inventario/activos/{LAPTOP}/baja", {"confirmar": LAPTOP})

    assert (alta.status_code, editar.status_code, baja.status_code) == (422, 422, 422)
    assert "No se guarda nada mientras assets.yaml" in alta.text
    assert "disabled" in alta.text
    assert snapshot_of(broken) == before


def test_con_un_system_invalido_tambien_se_bloquea(web, example_store):
    example_store.write_text("scope/system.yaml", "name: [")
    before = snapshot_of(example_store)

    listing = web.get("/inventario")

    assert listing.status_code == 200 and "system.yaml" in listing.text
    assert_no_write_controls(listing.text)
    assert web.post("/inventario/alta", PROVIDER).status_code == 422
    assert snapshot_of(example_store) == before


def test_un_inventario_valido_con_avisos_de_coherencia_avisa_pero_deja_escribir(web, example_store):
    example_store.write_text(
        "scope/services.yaml", "services:\n  - id: s\n    name: S\n    assets: [fantasma]\n"
    )

    html = web.get("/inventario").text

    assert "Avisos de coherencia." in html and "/inventario/alta" in html
    assert web.post("/inventario/alta", PROVIDER).status_code == 303


# --- K9: edición -------------------------------------------------------------------------------


def test_el_formulario_de_edicion_trae_los_valores_actuales_y_el_aviso(web, example_store):
    approve(example_store)

    response = web.get(f"/inventario/activos/{LAPTOP}?editar=1")

    assert response.status_code == 200
    html = response.text
    assert f'action="/inventario/activos/{LAPTOP}/editar"' in html
    assert f'value="{LAPTOP_HOST}"' in html and 'value="macOS 15"' in html
    assert "Guardar revoca la aprobación del alcance" in html


def test_cambiar_un_campo_conserva_el_resto_y_lo_muestra_la_ficha(web, example_store):
    before = assets_of(example_store)[LAPTOP]

    response = web.post(f"/inventario/activos/{LAPTOP}/editar", {"asset_os": "macOS 16"})

    assert response.status_code == 303
    assert assets_of(example_store)[LAPTOP] == {**before, "os": "macOS 16"}
    landing = web.get(response.headers["location"]).text
    assert "Cambios guardados." in landing and "macOS 16" in landing


def test_cambiar_el_despliegue(web, example_store):
    web.post(f"/inventario/activos/{LAPTOP}/editar", {"asset_deployment": "planned"})

    assert assets_of(example_store)[LAPTOP]["deployment"] == "planned"
    assert "Planificado" in text(row_of(web.get("/inventario").text, LAPTOP))


def test_excluir_con_justificacion_y_volver_a_incluir(web, example_store):
    web.post(
        f"/inventario/activos/{LAPTOP}/editar", {"scope": "excluded", "justification": "Se retira"}
    )
    listed = text(row_of(web.get("/inventario").text, LAPTOP))
    assert "Se retira" in listed and "Excluido" in listed

    web.post(f"/inventario/activos/{LAPTOP}/editar", {"scope": "included"})
    system = system_of(example_store)
    assert LAPTOP in system["included_assets"]
    assert not [e for e in system["exclusions"] if e["ref"] == LAPTOP]


def test_excluir_con_justificacion_vacia_da_422_y_no_cambia_nada(web, example_store):
    before = snapshot_of(example_store)

    response = web.post(
        f"/inventario/activos/{LAPTOP}/editar", {"scope": "excluded", "justification": " "}
    )

    assert response.status_code == 422
    assert "Editar el activo" in response.text  # la ficha se repinta en modo edición
    assert snapshot_of(example_store) == before


def test_vaciar_un_campo_obligatorio_da_422_y_no_cambia_nada(web, example_store):
    before = snapshot_of(example_store)

    response = web.post(f"/inventario/activos/{LAPTOP}/editar", {"asset_owner": ""})

    assert response.status_code == 422 and snapshot_of(example_store) == before


def test_editar_con_el_alcance_aprobado_exige_aceptar_y_revoca(web, example_store):
    approve(example_store)
    before = snapshot_of(example_store)

    refused = web.post(f"/inventario/activos/{LAPTOP}/editar", {"asset_os": "macOS 16"})
    assert refused.status_code == 422 and snapshot_of(example_store) == before
    assert scope_status(example_store).state is ScopeState.APPROVED

    done = web.post(
        f"/inventario/activos/{LAPTOP}/editar", {"asset_os": "macOS 16", "acepto_revocacion": "1"}
    )
    assert done.status_code == 303
    assert scope_status(example_store).state is ScopeState.NOT_APPROVED


def test_editar_queda_auditado(web, audit):
    web.post(f"/inventario/activos/{LAPTOP}/editar", {"asset_os": "macOS 16"})

    line = next(r.message for r in audit.records if "action=editar" in r.message)
    assert f"step=inventario action=editar asset={LAPTOP}" in line and "macOS 16" not in line


def test_editar_un_activo_inexistente_es_404(web):
    assert web.post("/inventario/activos/no-existe/editar", {"asset_os": "x"}).status_code == 404


def test_una_accion_desconocida_se_deniega_y_no_escribe(web, example_store):
    before = snapshot_of(example_store)

    assert web.post(f"/inventario/activos/{LAPTOP}/borrar", {}).status_code == 403
    assert snapshot_of(example_store) == before


# --- texto honesto y HTML seguro ---------------------------------------------------------------


@pytest.mark.parametrize("status", [CheckStatus.FAIL, CheckStatus.ERROR, CheckStatus.UNKNOWN])
def test_un_estado_no_favorable_nunca_lleva_tono_ok(web, example_store, status):
    record(example_store, "endpoint", [result(SERVER, status)])

    assert not favorable(row_of(web.get("/inventario").text, SERVER))
    assert not favorable(web.get(f"/inventario/activos/{SERVER}").text)


def test_un_pass_de_una_ejecucion_incompleta_no_lleva_tono_ok(web, example_store):
    record(example_store, "endpoint", [result(SERVER, CheckStatus.PASS)], complete=False)

    html = web.get("/inventario").text

    assert "PASS parcial" in row_of(html, SERVER) and not favorable(row_of(html, SERVER))
    detail = web.get(f"/inventario/activos/{SERVER}").text
    assert "Resultados incompletos" in detail


@pytest.mark.parametrize("partial", ["incompleta", "historial-ilegible"])
def test_la_ficha_no_pinta_tono_favorable_en_filas_pass_con_cobertura_parcial(
    web, example_store, partial
):
    record(
        example_store,
        "endpoint",
        [result(SERVER, CheckStatus.PASS)],
        complete=partial != "incompleta",
    )
    if partial == "historial-ilegible":
        example_store.write_text("evidence/checks/github/roto.json", "no es json")

    detail = web.get(f"/inventario/activos/{SERVER}").text

    assert "PASS parcial" in detail
    assert not favorable(detail)


def test_los_valores_del_inventario_se_escapan_y_no_hay_scripts(web, example_store):
    evil = "<img src=x onerror=alert(1)>"
    web.post("/inventario/alta", PROVIDER | {"asset_id": "p-evil", "asset_service": evil})

    pages = [
        web.get("/inventario").text,
        web.get("/inventario/activos/p-evil").text,
        web.get("/inventario/activos/p-evil?editar=1").text,
    ]

    for html in pages:
        assert evil not in html
        assert not re.search(r"<script(?![^>]*\bsrc=)", html)  # sin scripts en línea
    assert "&lt;img src=x onerror=alert(1)&gt;" in pages[1]


# --- navegación y texto honesto ----------------------------------------------------------------


def test_la_navegacion_principal_enlaza_el_inventario(web):
    assert 'href="/inventario"' in web.get("/").text


@pytest.mark.parametrize(
    "path",
    [
        "/inventario",
        "/inventario/alta?tipo=laptop",
        f"/inventario/activos/{LAPTOP}",
        f"/inventario/activos/{LAPTOP}?editar=1",
        f"/inventario/activos/{LAPTOP}?baja=1",
    ],
)
def test_ninguna_pantalla_declara_conformidad(web, example_store, path):
    record(example_store, "endpoint", [result(LAPTOP, CheckStatus.PASS)])

    response = web.get(path)

    assert response.status_code == 200
    assert not FORBIDDEN.search(text(response.text))


# --- errores de la API en el primer 422 ----------------------------------------------------------


def test_un_hostname_duplicado_se_muestra_en_el_primer_422_del_alta(web, example_store):
    before = snapshot_of(example_store)
    laptop = {
        "asset_type": "laptop",
        "asset_id": "lap-dup",
        "asset_hostname": LAPTOP_HOST,
        "scope": "included",
    }

    response = web.post("/inventario/alta", laptop)

    assert response.status_code == 422
    assert "hostname" in response.text and 'role="alert"' in response.text
    assert snapshot_of(example_store) == before


def test_un_secreto_se_muestra_en_el_primer_422_sin_el_valor(web, example_store):
    before = snapshot_of(example_store)
    secret = "AKIAIOSFODNN7EXAMPLE"

    response = web.post("/inventario/alta", PROVIDER | {"asset_service": secret})

    assert response.status_code == 422
    assert "parece contener un secreto" in response.text
    assert snapshot_of(example_store) == before


def test_los_errores_de_campo_y_de_la_api_salen_juntos_en_el_primer_422(web, example_store):
    approve(example_store)
    laptop = {
        "asset_type": "laptop",
        "asset_id": "lap-dup",
        "asset_hostname": LAPTOP_HOST,
        "scope": "included",
    }

    response = web.post("/inventario/alta", laptop)

    assert response.status_code == 422
    assert "Guardar revoca la aprobación del alcance" in response.text
    assert "hostname" in response.text and 'role="alert"' in response.text
