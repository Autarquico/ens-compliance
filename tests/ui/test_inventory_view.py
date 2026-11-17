"""Vista del inventario: contexto de cada pantalla y handlers, sin depender de las plantillas.

Las plantillas se sustituyen por un registro que guarda el nombre y el contexto de cada
renderizado; así se prueba lo que la vista entrega, no cómo se pinta.
"""

import dataclasses
import logging
import threading
import time
from datetime import timedelta
from urllib.parse import quote

import pytest
import yaml
from agent.protocol_support import (
    ENDPOINT_CONFIG,
    LAPTOP,
    LAPTOP_HOST,
    LAPTOP_UUID,
    SERVER,
    SERVER_HOST,
)
from starlette.testclient import TestClient

from ens_evidence.agent.node_key import normalize_host_identifier
from ens_evidence.agent.results import contact_result, write_result
from ens_evidence.checks.model import StoredCheckRun
from ens_evidence.checks.store import record_run
from ens_evidence.models.contracts import CheckResult
from ens_evidence.models.enums import CheckStatus, Role
from ens_evidence.scope import ScopeState, approve_scope, scope_status
from ens_evidence.ui.app import create_ui_app
from ens_evidence.ui.authz import INVENTORY_ACTIONS, OPERATIONS, ByParam
from ens_evidence.ui.roles import ROLES_KEY, Grant, dump_roles
from ens_evidence.ui.routes import ui_routes
from ens_evidence.ui.store import UiStore
from ens_evidence.ui.views import check_history
from ens_evidence.ui.views import inventory as view
from ens_evidence.ui.wizard import scope as wizard_scope
from ens_evidence.ui.wizard.base import StepInput
from ens_evidence.workspace import LocalStore

from .conftest import NOW, ORIGIN, Session, jwks_for, snapshot_of

SHA = "a" * 64
HOST_ID = normalize_host_identifier(LAPTOP_UUID)
SECRET_REASON = "RAZON-CON-VALOR-SENSIBLE"
REVOCATION_REASON = "MOTIVO-DE-REVOCACION"
PROVIDER = {
    "asset_type": "provider",
    "asset_id": "prov-x",
    "asset_service": "Correo",
    "asset_function": "Envío de correo",
    "asset_dependency": "Alta",
    "scope": "included",
}
# Claves que `page_context` añade a todas las pantallas: no son del contrato de la vista.
COMMON_KEYS = {
    "product_name",
    "identity_email",
    "nav_active",
    "page_title",
    "monitoring_banner",
    "permissions",
    "global_notices",
    "identity",
}


class Rendered:
    """Sustituye al entorno de plantillas: guarda (plantilla, contexto) de cada renderizado."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def get_template(self, name: str):
        calls = self.calls

        class Template:
            @staticmethod
            def render(**context) -> str:
                calls.append((name, context))
                return "<p>ok</p>"

        return Template()

    @property
    def last(self) -> tuple[str, dict]:
        return self.calls[-1]

    @property
    def context(self) -> dict:
        return self.last[1]


@dataclasses.dataclass
class Ui:
    session: Session
    rendered: Rendered
    store: LocalStore

    def get(self, path: str, **kwargs):
        return self.session.get(path, **kwargs)

    def post(self, path: str, data: dict):
        return self.session.post(path, data)

    @property
    def context(self) -> dict:
        return self.rendered.context


def build_ui(config, store, key, token, *, raise_errors: bool = True) -> Ui:
    app = create_ui_app(config, store, lambda: NOW, lambda: jwks_for(key), routes=ui_routes())
    rendered = Rendered()
    app.state.templates = rendered
    client = TestClient(app, base_url=ORIGIN, raise_server_exceptions=raise_errors)
    return Ui(Session(client, token), rendered, store)


@pytest.fixture
def ui(config, example_store, key, token) -> Ui:
    return build_ui(config, example_store, key, token)


@pytest.fixture
def audit(caplog):
    caplog.set_level(logging.INFO, logger="ens_evidence.ui.audit")
    return caplog


def record(store, connector: str, results: list[CheckResult], *, complete=True, ago=1) -> None:
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


def result(
    asset: str, status: CheckStatus, check: str = "HOST-001", reason: str = "ok"
) -> CheckResult:
    return CheckResult(
        check_id=check,
        asset_id=asset,
        status=status,
        evaluated_at=NOW,
        evidence_ids=["EV-1"],
        reason=reason,
        reason_code="some_code" if status is not CheckStatus.PASS else None,
    )


def rows_by_id(ui: Ui) -> dict:
    return {r.id: r for r in ui.context["page"].rows}


def approve(store) -> None:
    approve_scope(store, "Ana Ruiz", Role.SYSTEM_OWNER, NOW)


def assets_of(store) -> dict[str, dict]:
    document = yaml.safe_load(store.read_text("scope/assets.yaml"))
    return {a["id"]: a for a in document["assets"]}


def system_of(store) -> dict:
    return yaml.safe_load(store.read_text("scope/system.yaml"))


def writes(path_id: str, action: str) -> str:
    return f"/inventario/activos/{quote(path_id, safe='')}/{action}"


# --- rutas y autorización ------------------------------------------------------------------------


def test_las_acciones_de_la_vista_son_exactamente_las_de_la_politica():
    assert set(view.ACTIONS) == set(INVENTORY_ACTIONS)
    entry = OPERATIONS[("POST", "/inventario/activos/{activo}/{accion}")]
    assert isinstance(entry, ByParam) and entry.policies is INVENTORY_ACTIONS
    assert ("POST", "/inventario/alta") in OPERATIONS


def test_las_rutas_no_reservan_ningun_id():
    paths = {(r.path, tuple(sorted(r.methods - {"HEAD"}))) for r in view.INVENTORY_ROUTES}
    assert paths == {
        ("/inventario", ("GET",)),
        ("/inventario/alta", ("GET",)),
        ("/inventario/alta", ("POST",)),
        ("/inventario/activos/{activo}", ("GET",)),
        ("/inventario/activos/{activo}/{accion}", ("POST",)),
    }


# --- listado -------------------------------------------------------------------------------------


def test_listado_sin_historial_dice_sin_evidencia_en_todas_las_filas(ui):
    assert ui.get("/inventario").status_code == 200

    name, context = ui.rendered.last
    assert name == "views/inventario.html"
    page = context["page"]
    assert page.total == len(page.rows) == 13
    assert all(r.evidence is None for r in page.rows)
    assert context["rows"] is page.rows and context["reconciliation"] is page.reconciliation
    assert page.writes_blocked is False and page.file_errors == ()


def test_listado_muestra_el_peor_estado_con_fecha_y_fuente(ui, example_store):
    record(
        example_store,
        "endpoint",
        [
            result(SERVER, CheckStatus.PASS),
            result(SERVER, CheckStatus.FAIL, "HOST-002"),
        ],
    )
    ui.get("/inventario")

    rows = rows_by_id(ui)
    evidence = rows[SERVER].evidence
    assert (evidence.status, evidence.coverage, evidence.favorable) == ("FAIL", "completa", False)
    assert evidence.connector == "endpoint" and evidence.checks == 2
    assert evidence.finished_at == "2026-11-08 11:00 UTC"
    assert evidence.sentence == "Último resultado de 2 checks de endpoint el 2026-11-08 11:00 UTC"
    assert evidence.evidence_ids == ("EV-1",)
    assert rows[LAPTOP].evidence is None


def test_un_pass_completo_es_favorable_solo_como_resultado_de_checks(ui, example_store):
    record(example_store, "endpoint", [result(SERVER, CheckStatus.PASS)])
    ui.get("/inventario")

    evidence = rows_by_id(ui)[SERVER].evidence
    assert (evidence.label, evidence.coverage, evidence.favorable) == (
        "Sin fallos",
        "completa",
        True,
    )
    assert evidence.sentence.startswith("Último resultado de 1 check de endpoint")


def test_pass_con_el_historial_de_otro_conector_ilegible_es_parcial(ui, example_store):
    record(example_store, "endpoint", [result(SERVER, CheckStatus.PASS)])
    example_store.write_text("evidence/checks/github/roto.json", "no es json")
    ui.get("/inventario")

    page = ui.context["page"]
    evidence = rows_by_id(ui)[SERVER].evidence
    assert (evidence.label, evidence.coverage, evidence.favorable) == (
        "Sin fallos, parcial",
        "parcial",
        False,
    )
    assert any("github" in reason for reason in evidence.partial_reasons)
    assert any("github" in note for note in page.history_notes)  # nunca se oculta en silencio


def test_pass_de_una_ejecucion_incompleta_es_parcial(ui, example_store):
    record(example_store, "endpoint", [result(SERVER, CheckStatus.PASS)], complete=False)
    ui.get("/inventario")

    evidence = rows_by_id(ui)[SERVER].evidence
    assert (evidence.label, evidence.favorable) == ("Sin fallos, parcial", False)
    assert any("incompleta" in reason for reason in evidence.partial_reasons)


def test_el_resumen_junta_conectores_y_toma_el_peor(ui, example_store):
    record(example_store, "endpoint", [result(SERVER, CheckStatus.PASS)])
    record(example_store, "github", [result(SERVER, CheckStatus.UNKNOWN, "GH-1")], ago=2)
    ui.get("/inventario")

    evidence = rows_by_id(ui)[SERVER].evidence
    assert evidence.status == "UNKNOWN" and evidence.connector == "endpoint, github"
    assert evidence.checks == 2 and evidence.sentence.count("de ") >= 2


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("tipo=server", {SERVER}),
        ("alcance=excluded", {"lap-personal"}),
        ("estado=FAIL", {SERVER}),
        ("estado=sin-evidencia&tipo=laptop", {LAPTOP, "lap-personal"}),
        ("despliegue=planned", {"bk-externo"}),
        ("tipo=inventado", None),  # un valor desconocido no filtra
    ],
)
def test_filtros_por_tipo_despliegue_alcance_y_estado(ui, example_store, query, expected):
    record(example_store, "endpoint", [result(SERVER, CheckStatus.FAIL)])
    ui.get(f"/inventario?{query}")

    page = ui.context["page"]
    assert page.total == 13
    if expected is None:
        assert len(page.rows) == 13 and page.filters.tipo == ""
    else:
        assert {r.id for r in page.rows} == expected


def test_el_listado_marca_el_alcance_y_la_justificacion(ui):
    ui.get("/inventario")

    rows = rows_by_id(ui)
    assert rows["lap-personal"].scope == "excluded"
    assert "personal" in rows["lap-personal"].justification
    assert rows[LAPTOP].scope == "included" and rows[LAPTOP].justification is None


def test_el_listado_enlaza_la_ficha_con_el_id_codificado(ui, example_store):
    ui.post("/inventario/alta", PROVIDER | {"asset_id": "prov#1+ñ"})
    ui.get("/inventario")

    assert rows_by_id(ui)["prov#1+ñ"].href == "/inventario/activos/prov%231%2B%C3%B1"


def test_hecho_pinta_el_aviso_solo_si_es_uno_conocido(ui):
    ui.get("/inventario?hecho=baja&revocada=1")
    assert ui.context["saved"] == view.SAVED_TEXTS["baja"]
    assert ui.context["revoked_notice"] == view.REVOKED_NOTICE

    ui.get("/inventario?hecho=<script>&revocada=1")
    assert ui.context["saved"] is None and ui.context["revoked_notice"] is None


# --- ficha ---------------------------------------------------------------------------------------


def test_ficha_con_servicios_dependencias_y_resultados_por_conector(ui, example_store):
    record(example_store, "endpoint", [result("app-facturas", CheckStatus.PASS)])
    record(example_store, "github", [result("app-facturas", CheckStatus.FAIL, "GH-1")], ago=2)
    assert ui.get("/inventario/activos/app-facturas").status_code == 200

    name, context = ui.rendered.last
    detail = context["detail"]
    assert name == "views/inventario_activo.html"
    assert dict(detail.fields)["Dominio"] == "facturas.taller-norte.example"
    assert [s.id for s in detail.services] == ["svc-facturacion"]
    assert [d.id for d in detail.dependencies] == ["dep-app-db"]
    assert [(r.connector, r.finished_at) for r in detail.results] == [
        ("endpoint", "2026-11-08 11:00 UTC"),
        ("github", "2026-11-08 10:00 UTC"),
    ]
    assert detail.results[0].rows[0].evidence_ids == ("EV-1",)
    assert detail.referenced and detail.references_known and not detail.removable
    assert context["form"] is None and context["removal"] is None


def test_ficha_de_un_activo_inexistente_es_404(ui):
    assert ui.get("/inventario/activos/no-existe").status_code == 404


def test_ficha_con_un_id_que_pide_codificacion(ui):
    ui.post("/inventario/alta", PROVIDER | {"asset_id": "prov#1+ñ"})

    assert ui.get("/inventario/activos/prov%231%2B%C3%B1").status_code == 200
    assert ui.context["detail"].row.id == "prov#1+ñ"
    assert ui.get("/inventario/activos/nuevo").status_code == 404  # sin ids reservados
    ui.post("/inventario/alta", PROVIDER | {"asset_id": "nuevo"})
    assert ui.get("/inventario/activos/nuevo").status_code == 200


def test_ficha_editar_y_baja_se_ofrecen_solo_con_permiso_y_inventario_valido(ui, example_store):
    ui.get(f"/inventario/activos/{LAPTOP}?editar=1")
    form = ui.context["form"]
    assert (form.mode, form.type, form.asset_id) == ("editar", "laptop", LAPTOP)
    assert ui.context["values"]["asset_hostname"] == LAPTOP_HOST
    assert ui.context["values"]["asset_encrypted"] == "sí"
    assert ui.context["values"]["scope"] == "included"
    assert form.action == f"/inventario/activos/{LAPTOP}/editar"

    ui.get("/inventario/activos/db-facturas?baja=1")
    removal = ui.context["removal"]
    assert removal.blockers and {r.id for r in removal.references} == {
        "svc-facturacion",
        "dep-app-db",
    }

    example_store.write_text(ROLES_KEY, dump_roles({"ana@example.com": Grant(False, frozenset())}))
    ui.get(f"/inventario/activos/{LAPTOP}?editar=1")
    assert ui.context["form"] is None
    ui.get(f"/inventario/activos/{LAPTOP}?baja=1")
    assert ui.context["removal"] is None


def test_la_baja_cerrada_si_no_se_pueden_leer_las_referencias(ui, example_store):
    example_store.write_text("scope/services.yaml", "services: [")
    ui.get("/inventario/activos/lap-personal?baja=1")

    detail, removal = ui.context["detail"], ui.context["removal"]
    assert not detail.references_known and not detail.removable
    assert view.REMOVAL_UNKNOWN in removal.blockers


def test_la_baja_de_un_activo_libre_se_ofrece(ui):
    ui.get("/inventario/activos/lap-personal?baja=1")

    assert ui.context["removal"].blockers == ()
    assert ui.context["detail"].removable


# --- formulario de alta --------------------------------------------------------------------------


def test_alta_sin_tipo_pide_el_tipo_y_conserva_lo_prellenado(ui):
    assert ui.get("/inventario/alta?id=lap-nueva&hostname=lap-nueva.x").status_code == 200

    name, context = ui.rendered.last
    form = context["form"]
    assert name == "views/inventario_form.html"
    assert (form.mode, form.type, form.fields) == ("nuevo", None, ())
    assert {c.value for c in form.asset_types} >= {"laptop", "server", "provider"}
    assert context["values"]["asset_id"] == "lap-nueva"
    assert context["values"]["asset_hostname"] == "lap-nueva.x"


def test_alta_prellenada_de_un_portatil_tiene_el_campo_hostname(ui):
    ui.get("/inventario/alta?tipo=laptop&id=lap-nueva&hostname=lap-nueva.x")

    form, values = ui.context["form"], ui.context["values"]
    names = [f.name for f in form.fields]
    assert "asset_hostname" in names and "asset_owner" in names
    assert next(f for f in form.fields if f.name == "asset_hostname").required is False
    assert next(f for f in form.fields if f.name == "asset_encrypted").kind == "bool"
    assert values["asset_hostname"] == "lap-nueva.x" and values["scope"] == "included"
    assert form.deployment == "deployed"


def test_el_aviso_de_revocacion_solo_aparece_con_el_alcance_aprobado(ui, example_store):
    ui.get("/inventario/alta?tipo=laptop")
    assert ui.context["form"].approval_warning is None

    approve(example_store)
    ui.get("/inventario/alta?tipo=laptop")
    warning = ui.context["form"].approval_warning
    assert warning.approved_by == "Ana Ruiz" and warning.approved_at == "2026-11-08 12:00 UTC"


# --- alta ----------------------------------------------------------------------------------------


def test_alta_guarda_el_activo_en_el_inventario_y_el_alcance_y_audita(ui, example_store, audit):
    response = ui.post("/inventario/alta", PROVIDER)

    assert response.status_code == 303
    assert response.headers["location"] == "/inventario/activos/prov-x?hecho=nuevo"
    assert assets_of(example_store)["prov-x"]["service"] == "Correo"
    assert "prov-x" in system_of(example_store)["included_assets"]
    line = next(r.message for r in audit.records if "action=nuevo" in r.message)
    assert "outcome=saved" in line and "step=inventario action=nuevo asset=prov-x" in line
    assert "scope/assets.yaml" in line and "Correo" not in line  # nunca valores


def test_alta_con_alcance_aprobado_exige_el_checkbox_y_no_escribe_sin_el(ui, example_store):
    approve(example_store)
    before = snapshot_of(example_store)

    response = ui.post("/inventario/alta", PROVIDER)

    assert response.status_code == 422
    name, context = ui.rendered.last
    assert name == "views/inventario_form.html"
    assert "acepto_revocacion" in context["errors"]
    assert context["form"].approval_warning is not None
    assert context["values"]["asset_id"] == "prov-x"  # el formulario se repinta con lo escrito
    assert snapshot_of(example_store) == before
    assert scope_status(example_store).state is ScopeState.APPROVED


def test_alta_con_el_checkbox_revoca_la_aprobacion(ui, example_store, audit):
    approve(example_store)

    response = ui.post("/inventario/alta", PROVIDER | {"acepto_revocacion": "on"})

    assert response.status_code == 303
    assert response.headers["location"] == "/inventario/activos/prov-x?hecho=nuevo&revocada=1"
    assert scope_status(example_store).state is ScopeState.NOT_APPROVED
    assert "prov-x" in assets_of(example_store)


def test_alta_de_un_portatil_conserva_el_hostname_prellenado(ui, example_store):
    form = {
        "asset_type": "laptop",
        "asset_id": "lap-nueva",
        "asset_hostname": "lap-nueva.taller-norte.example",
        "asset_os": "macOS 15",
        "asset_owner": "Luis",
        "asset_encrypted": "sí",
        "asset_usage": "Desarrollo",
        "asset_deployment": "planned",
        "scope": "included",
    }
    assert ui.post("/inventario/alta", form).status_code == 303

    saved = assets_of(example_store)["lap-nueva"]
    assert saved["hostname"] == "lap-nueva.taller-norte.example"
    assert (saved["encrypted"], saved["deployment"]) == (True, "planned")


def test_alta_excluida_exige_justificacion(ui, example_store):
    before = snapshot_of(example_store)
    response = ui.post("/inventario/alta", PROVIDER | {"scope": "excluded"})
    assert response.status_code == 422 and "justification" in ui.context["errors"]
    assert snapshot_of(example_store) == before

    ui.post("/inventario/alta", PROVIDER | {"scope": "excluded", "justification": "Solo pruebas"})
    exclusion = [e for e in system_of(example_store)["exclusions"] if e["ref"] == "prov-x"]
    assert exclusion[0]["justification"] == "Solo pruebas"


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"asset_type": ""}, "asset_type"),
        ({"asset_id": ""}, "asset_id"),
        ({"asset_id": "con espacio"}, "asset_id"),
        ({"asset_id": "a/b"}, "asset_id"),
        ({"asset_id": ".."}, "asset_id"),
        ({"scope": ""}, "scope"),
        ({"scope": "quizá"}, "scope"),
    ],
)
def test_alta_invalida_da_422_sin_escribir(ui, example_store, changes, field):
    before = snapshot_of(example_store)

    response = ui.post("/inventario/alta", PROVIDER | changes)

    assert response.status_code == 422
    assert field in ui.context["errors"]
    assert ui.rendered.last[0] == "views/inventario_form.html"
    assert snapshot_of(example_store) == before


def test_alta_de_un_id_existente_o_con_campos_que_faltan_se_rechaza(ui, example_store):
    before = snapshot_of(example_store)

    assert ui.post("/inventario/alta", PROVIDER | {"asset_id": LAPTOP}).status_code == 422
    assert any("ya existe" in e for e in ui.context["form_errors"])
    assert ui.post("/inventario/alta", PROVIDER | {"asset_function": ""}).status_code == 422
    assert ui.context["form_errors"]
    assert snapshot_of(example_store) == before


def test_alta_con_un_secreto_no_lo_repite_en_la_respuesta(ui, example_store):
    before = snapshot_of(example_store)

    response = ui.post("/inventario/alta", PROVIDER | {"asset_function": "AKIAABCDEFGHIJKLMNOP"})

    assert response.status_code == 422
    assert "AKIAABCDEFGHIJKLMNOP" not in repr(ui.context["form_errors"])
    assert snapshot_of(example_store) == before


# --- edición -------------------------------------------------------------------------------------


def test_editar_un_campo_conserva_el_resto_del_activo(ui, example_store):
    before = assets_of(example_store)[LAPTOP]

    response = ui.post(writes(LAPTOP, "editar"), {"asset_os": "macOS 16"})

    assert response.status_code == 303
    assert response.headers["location"] == f"/inventario/activos/{LAPTOP}?hecho=editar"
    saved = assets_of(example_store)[LAPTOP]
    assert saved == {**before, "os": "macOS 16"}  # hostname, despliegue y resto intactos
    assert LAPTOP in system_of(example_store)["included_assets"]


def test_editar_el_despliegue(ui, example_store):
    ui.post(writes(LAPTOP, "editar"), {"asset_deployment": "planned"})

    assert assets_of(example_store)[LAPTOP]["deployment"] == "planned"


def test_editar_con_un_opcional_en_blanco_lo_borra(ui, example_store):
    ui.post(writes(LAPTOP, "editar"), {"asset_hostname": ""})

    assert "hostname" not in assets_of(example_store)[LAPTOP]


def test_editar_vaciando_un_obligatorio_se_rechaza_y_no_escribe(ui, example_store):
    before = snapshot_of(example_store)

    response = ui.post(writes(LAPTOP, "editar"), {"asset_owner": ""})

    assert response.status_code == 422
    assert ui.rendered.last[0] == "views/inventario_activo.html"
    assert ui.context["form"].mode == "editar" and ui.context["form_errors"]
    assert snapshot_of(example_store) == before


def test_excluir_con_justificacion_y_volver_a_incluir(ui, example_store):
    ui.post(writes(LAPTOP, "editar"), {"scope": "excluded", "justification": "Se retira"})
    system = system_of(example_store)
    assert LAPTOP not in system["included_assets"]
    assert [e["justification"] for e in system["exclusions"] if e["ref"] == LAPTOP] == ["Se retira"]

    ui.post(writes(LAPTOP, "editar"), {"scope": "included"})
    system = system_of(example_store)
    assert LAPTOP in system["included_assets"]
    assert not [e for e in system["exclusions"] if e["ref"] == LAPTOP]


def test_excluir_sin_justificacion_da_422_y_no_cambia_nada(ui, example_store):
    before = snapshot_of(example_store)

    response = ui.post(writes(LAPTOP, "editar"), {"scope": "excluded", "justification": " "})

    assert response.status_code == 422
    assert "justification" in ui.context["errors"]
    assert ui.context["values"]["scope"] == "excluded"
    assert snapshot_of(example_store) == before


def test_editar_con_alcance_aprobado_exige_el_checkbox(ui, example_store):
    approve(example_store)
    before = snapshot_of(example_store)

    assert ui.post(writes(LAPTOP, "editar"), {"asset_os": "macOS 16"}).status_code == 422
    assert "acepto_revocacion" in ui.context["errors"]
    assert snapshot_of(example_store) == before

    done = ui.post(writes(LAPTOP, "editar"), {"asset_os": "macOS 16", "acepto_revocacion": "on"})
    assert done.status_code == 303 and done.headers["location"].endswith("hecho=editar&revocada=1")
    assert scope_status(example_store).state is ScopeState.NOT_APPROVED


def test_editar_sin_cambios_no_escribe_ni_revoca(ui, example_store):
    approve(example_store)
    before = snapshot_of(example_store)

    response = ui.post(writes(LAPTOP, "editar"), {"acepto_revocacion": "on"})

    assert response.status_code == 303 and response.headers["location"].endswith(
        "hecho=sin-cambios"
    )
    assert snapshot_of(example_store) == before


def test_editar_un_activo_inexistente_es_404(ui):
    assert ui.post(writes("no-existe", "editar"), {"asset_os": "x"}).status_code == 404


def test_editar_ignora_un_id_distinto_en_el_formulario(ui, example_store):
    ui.post(writes(LAPTOP, "editar"), {"asset_id": "otro", "asset_os": "macOS 16"})

    assert "otro" not in assets_of(example_store)
    assert assets_of(example_store)[LAPTOP]["os"] == "macOS 16"


# --- baja ----------------------------------------------------------------------------------------


def test_baja_de_un_activo_referenciado_se_rechaza_nombrando_quien_lo_usa(ui, example_store):
    before = snapshot_of(example_store)

    response = ui.post(writes("db-facturas", "baja"), {"confirmar": "db-facturas"})

    assert response.status_code == 422
    context = ui.context
    text = " ".join(context["form_errors"])
    assert "svc-facturacion" in text and "dep-app-db" in text
    assert ui.rendered.last[0] == "views/inventario_activo.html"
    assert {r.id for r in context["removal"].references} == {"svc-facturacion", "dep-app-db"}
    assert snapshot_of(example_store) == before


def test_baja_pide_escribir_el_id(ui, example_store):
    before = snapshot_of(example_store)

    response = ui.post(writes("lap-personal", "baja"), {"confirmar": "otro"})

    assert response.status_code == 422 and "confirmar" in ui.context["errors"]
    assert ui.context["removal"] is not None
    assert snapshot_of(example_store) == before


def test_baja_de_un_activo_libre_lo_quita_del_inventario_y_de_las_exclusiones(
    ui, example_store, audit
):
    response = ui.post(writes("lap-personal", "baja"), {"confirmar": "lap-personal"})

    assert response.status_code == 303
    assert response.headers["location"] == "/inventario?hecho=baja"
    assert "lap-personal" not in assets_of(example_store)
    assert not [e for e in system_of(example_store)["exclusions"] if e["ref"] == "lap-personal"]
    line = next(r.message for r in audit.records if "action=baja" in r.message)
    assert "asset=lap-personal" in line and "outcome=saved" in line


def test_baja_con_alcance_aprobado_exige_el_checkbox(ui, example_store):
    approve(example_store)
    before = snapshot_of(example_store)
    data = {"confirmar": "lap-personal"}

    assert ui.post(writes("lap-personal", "baja"), data).status_code == 422
    assert "acepto_revocacion" in ui.context["errors"]
    assert snapshot_of(example_store) == before

    assert (
        ui.post(writes("lap-personal", "baja"), data | {"acepto_revocacion": "on"}).status_code
        == 303
    )
    assert scope_status(example_store).state is ScopeState.NOT_APPROVED


@pytest.mark.parametrize("broken", ["scope/services.yaml", "scope/dependencies.yaml"])
def test_baja_con_referencias_ilegibles_no_se_hace(ui, example_store, broken):
    example_store.write_text(broken, "esto: [sin cerrar")
    before = snapshot_of(example_store)

    response = ui.post(writes("lap-personal", "baja"), {"confirmar": "lap-personal"})

    assert response.status_code == 422 and ui.context["form_errors"]
    assert snapshot_of(example_store) == before


def test_el_id_con_caracteres_de_url_se_da_de_alta_edita_y_baja(ui, example_store):
    ui.post("/inventario/alta", PROVIDER | {"asset_id": "prov#1+ñ"})
    assert "prov#1+ñ" in assets_of(example_store)

    assert ui.post(writes("prov#1+ñ", "editar"), {"asset_service": "Otro"}).status_code == 303
    assert assets_of(example_store)["prov#1+ñ"]["service"] == "Otro"

    assert ui.post(writes("prov#1+ñ", "baja"), {"confirmar": "prov#1+ñ"}).status_code == 303
    assert "prov#1+ñ" not in assets_of(example_store)


# --- inventario inválido (la puerta del servidor) ------------------------------------------------


@pytest.fixture
def broken_assets(example_store):
    example_store.write_text("scope/assets.yaml", "assets:\n  - id: x\n    type: inventado\n")
    return example_store


def test_listado_con_assets_invalidos_muestra_los_errores_y_bloquea_la_escritura(ui, broken_assets):
    assert ui.get("/inventario").status_code == 200

    page = ui.context["page"]
    assert page.writes_blocked and page.rows == ()
    assert any("assets.yaml" in e for e in page.file_errors)


def test_con_assets_invalidos_los_tres_post_dan_422_sin_escribir(ui, broken_assets):
    before = snapshot_of(broken_assets)

    assert ui.post("/inventario/alta", PROVIDER).status_code == 422
    assert ui.rendered.last[0] == "views/inventario_form.html"
    assert ui.context["writes_blocked"] and view.BLOCKED_NOTICE in ui.context["form_errors"]

    assert ui.post(writes(LAPTOP, "editar"), {"asset_os": "x"}).status_code == 422
    assert ui.post(writes(LAPTOP, "baja"), {"confirmar": LAPTOP}).status_code == 422
    assert ui.rendered.last[0] == "views/inventario.html"  # no hay ficha que pintar
    assert ui.context["page"].writes_blocked and ui.context["form_errors"]
    assert snapshot_of(broken_assets) == before


def test_con_system_invalido_tambien_se_bloquea(ui, example_store):
    example_store.write_text("scope/system.yaml", "name: [")
    before = snapshot_of(example_store)

    assert ui.post("/inventario/alta", PROVIDER).status_code == 422
    assert ui.get("/inventario").status_code == 200
    assert ui.context["page"].writes_blocked
    assert {r.scope for r in ui.context["page"].rows} == {"unknown"}
    assert snapshot_of(example_store) == before


def test_los_errores_solo_de_integridad_avisan_pero_no_bloquean(ui, example_store):
    example_store.write_text(
        "scope/services.yaml", "services:\n  - id: s\n    name: S\n    assets: [fantasma]\n"
    )
    ui.get("/inventario")
    page = ui.context["page"]
    assert page.integrity_warnings and not page.writes_blocked

    assert ui.post("/inventario/alta", PROVIDER).status_code == 303


# --- exclusión y fallos (K2, K4) -----------------------------------------------------------------


class GatedStore(UiStore):
    """Se detiene en su primera escritura hasta que el test la libera."""

    def __init__(self, inner) -> None:
        super().__init__(inner)
        self.entered = threading.Event()
        self.release = threading.Event()
        self._first = True

    def write_text(self, key: str, text: str) -> None:
        if self._first:
            self._first = False
            self.entered.set()
            assert self.release.wait(5)
        super().write_text(key, text)


def test_dos_altas_intercaladas_no_se_pisan_la_segunda_ve_lo_de_la_primera(example_store):
    gated = GatedStore(example_store)
    outcomes: dict[str, object] = {}
    form = {k: [v] for k, v in PROVIDER.items()}

    def run(name: str, store: UiStore) -> None:
        try:
            outcomes[name] = view._create(store, NOW, form)
        except view.Rejected as rejected:
            outcomes[name] = rejected

    first = threading.Thread(target=run, args=("first", gated))
    first.start()
    assert gated.entered.wait(5)
    second = threading.Thread(target=run, args=("second", UiStore(example_store)))
    second.start()
    time.sleep(0.2)  # la segunda ya está esperando el lock, si lo hay
    gated.release.set()
    first.join(5)
    second.join(5)

    assert outcomes["first"] is False
    rejected = outcomes["second"]
    assert isinstance(rejected, view.Rejected)
    assert any("ya existe" in e for e in rejected.form_errors)
    document = yaml.safe_load(example_store.read_text("scope/assets.yaml"))
    assert [a["id"] for a in document["assets"]].count("prov-x") == 1


def test_un_alta_del_asistente_y_otra_del_inventario_intercaladas_no_se_pisan(example_store):
    gated = GatedStore(example_store)
    form = {k: [v] for k, v in PROVIDER.items()}
    outcomes: dict[str, object] = {}

    def wizard() -> None:
        outcomes["wizard"] = wizard_scope._add_asset(StepInput(gated, NOW, "ana@x", form=form))

    def inventory() -> None:
        try:
            outcomes["inventory"] = view._create(UiStore(example_store), NOW, form)
        except view.Rejected as rejected:
            outcomes["inventory"] = rejected

    first = threading.Thread(target=wizard)
    first.start()
    assert gated.entered.wait(5)
    second = threading.Thread(target=inventory)
    second.start()
    time.sleep(0.2)  # el inventario ya está esperando el lock del alcance, si lo hay
    gated.release.set()
    first.join(5)
    second.join(5)

    assert outcomes["wizard"] is None
    rejected = outcomes["inventory"]
    assert isinstance(rejected, view.Rejected)
    assert any("ya existe" in e for e in rejected.form_errors)
    document = yaml.safe_load(example_store.read_text("scope/assets.yaml"))
    assert [a["id"] for a in document["assets"]].count("prov-x") == 1


def test_un_fallo_tras_escribir_deja_el_evento_failed_y_el_inventario_como_estaba(
    config, example_store, key, token, audit, monkeypatch
):
    ui = build_ui(config, example_store, key, token, raise_errors=False)
    before = snapshot_of(example_store)
    real = LocalStore.write_text

    def flaky(self, key_, text):
        if key_ == "scope/system.yaml":
            raise OSError("disco lleno")
        real(self, key_, text)

    monkeypatch.setattr(LocalStore, "write_text", flaky)

    response = ui.post("/inventario/alta", PROVIDER)

    assert response.status_code == 500
    line = next(r.message for r in audit.records if "outcome=failed" in r.message)
    assert "step=inventario action=nuevo asset=prov-x" in line and "scope/assets.yaml" in line
    assert "Correo" not in line
    monkeypatch.undo()
    assert snapshot_of(example_store) == before


def test_un_rechazo_no_deja_linea_de_auditoria(ui, example_store, audit):
    ui.post("/inventario/alta", PROVIDER | {"asset_id": ""})

    assert not [r for r in audit.records if "step=inventario" in r.message]


# --- reconciliación con los agentes (K5) ---------------------------------------------------------

LAPTOP_ONLY = f"""\
assets:
  - id: {SERVER}
    type: server
    deployment: deployed
    hostname: {SERVER_HOST}
    function: Aplicaciones
    os: Debian 12
    environment: producción
    location: Oficina
"""


@pytest.fixture
def agents(example_store):
    example_store.write_text("connectors/endpoint.yaml", yaml.safe_dump(ENDPOINT_CONFIG))
    return example_store


def admit(store, *, ago=timedelta(hours=30), **record_fields):
    record_fields.setdefault("last_admission_at", (NOW - ago).isoformat())
    store.write_text("agents/registry.yaml", yaml.safe_dump({"agents": {LAPTOP: record_fields}}))


def contact(store, *, ago=timedelta(hours=1), generation=1):
    result = contact_result(LAPTOP, HOST_ID, "darwin", generation, now=NOW - ago)
    return write_result(store, result, now=NOW)


def reconciliation(ui) -> dict:
    ui.get("/inventario")
    return {r.asset_id: r for r in ui.context["reconciliation"].rows}


def test_sin_endpoint_yaml_la_reconciliacion_lo_dice_y_la_pagina_responde(ui):
    assert ui.get("/inventario").status_code == 200

    reconciliation = ui.context["reconciliation"]
    assert reconciliation.rows == () and reconciliation.notes == (view.NO_AGENTS_NOTE,)


def test_equipo_admitido_con_contacto_reciente_no_genera_discrepancia(ui, agents):
    admit(agents)
    contact(agents)

    rows = reconciliation(ui)

    assert LAPTOP not in rows
    assert rows[SERVER].kind == "not_enrolled"  # desplegado y sin alta
    assert rows[SERVER].asset_href == "/inventario/activos/srv-app"


def test_admitido_sin_contacto_no_se_da_por_que_reporta(ui, agents):
    admit(agents)

    row = reconciliation(ui)[LAPTOP]

    assert row.kind == "admitted_silent"
    assert row.detail == "Sin contacto desde el alta."


def test_contacto_antiguo_es_admitido_sin_contacto_reciente(ui, agents):
    admit(agents)
    evidence_id = contact(agents, ago=timedelta(hours=30))

    row = reconciliation(ui)[LAPTOP]

    assert row.kind == "admitted_silent"
    assert "2026-11-07 06:00 UTC" in row.detail and evidence_id in row.detail


def test_alta_revocada_cuenta_como_sin_alta(ui, agents):
    admit(agents, revoked_at=NOW.isoformat(), revoked_reason=REVOCATION_REASON)
    contact(agents)

    row = reconciliation(ui)[LAPTOP]

    assert row.kind == "not_enrolled" and row.detail == "El alta está revocada."
    assert REVOCATION_REASON not in repr(ui.context["reconciliation"])


def test_mismo_id_con_hostname_distinto_se_marca(ui, agents):
    admit(agents)
    contact(agents)
    text = agents.read_text("scope/assets.yaml").replace(LAPTOP_HOST, "otro.taller-norte.example")
    agents.write_text("scope/assets.yaml", text)

    row = reconciliation(ui)[LAPTOP]

    assert (row.kind, row.hostname_mismatch) == ("hostname_mismatch", True)
    assert (row.hostname, row.inventory_hostname) == (LAPTOP_HOST, "otro.taller-norte.example")


def test_admitido_y_en_silencio_con_hostname_distinto_es_una_sola_fila(ui, agents):
    admit(agents)
    text = agents.read_text("scope/assets.yaml").replace(LAPTOP_HOST, "otro.taller-norte.example")
    agents.write_text("scope/assets.yaml", text)

    row = reconciliation(ui)[LAPTOP]

    assert (row.kind, row.hostname_mismatch) == ("admitted_silent", True)


def test_equipo_que_reporta_y_no_esta_inventariado_ofrece_el_alta_prellenada(ui, agents):
    agents.write_text("scope/assets.yaml", LAPTOP_ONLY)
    admit(agents)
    contact(agents)

    row = reconciliation(ui)[LAPTOP]

    assert row.kind == "not_inventoried" and row.asset_href is None
    assert row.prefill_href == f"/inventario/alta?tipo=laptop&id={LAPTOP}&hostname={LAPTOP_HOST}"
    assert ui.get(row.prefill_href).status_code == 200
    assert ui.context["values"]["asset_hostname"] == LAPTOP_HOST
    assert ui.context["form"].type == "laptop"


def test_equipo_admitido_sin_contacto_y_no_inventariado_tambien_se_ofrece(ui, agents):
    agents.write_text("scope/assets.yaml", LAPTOP_ONLY)
    admit(agents)

    row = reconciliation(ui)[LAPTOP]

    assert row.kind == "not_inventoried_silent" and row.prefill_href


def test_el_alta_prellenada_guarda_el_hostname(ui, agents):
    agents.write_text("scope/assets.yaml", LAPTOP_ONLY)
    form = {
        "asset_type": "laptop",
        "asset_id": LAPTOP,
        "asset_hostname": LAPTOP_HOST,
        "asset_os": "macOS 15",
        "asset_owner": "Ana",
        "asset_encrypted": "sí",
        "asset_usage": "Desarrollo",
        "scope": "included",
    }
    assert ui.post("/inventario/alta", form).status_code == 303
    assert assets_of(agents)[LAPTOP]["hostname"] == LAPTOP_HOST


def test_registro_de_agentes_corrupto_es_una_nota_no_un_error(ui, agents):
    agents.write_text("agents/registry.yaml", "agents: [")

    assert ui.get("/inventario").status_code == 200
    reconciliation = ui.context["reconciliation"]
    assert reconciliation.rows == () and "registro de agentes" in reconciliation.notes[0]


def test_endpoint_yaml_corrupto_es_una_nota(ui, example_store):
    example_store.write_text("connectors/endpoint.yaml", "hosts: [")

    assert ui.get("/inventario").status_code == 200
    assert "endpoint.yaml" in ui.context["reconciliation"].notes[0]


def test_la_ficha_enlaza_el_panel_de_agentes_solo_de_los_equipos_inscritos(ui, agents):
    admit(agents)
    ui.get("/inventario")
    rows = rows_by_id(ui)

    assert rows[LAPTOP].agent_href == f"/agentes/{LAPTOP}"
    assert rows["app-facturas"].agent_href is None


# --- el contexto no lleva datos crudos (K8) ------------------------------------------------------

LEAKS = (
    LAPTOP_UUID,
    LAPTOP_UUID.upper(),
    HOST_ID,
    SECRET_REASON,
    REVOCATION_REASON,
    "included_assets",
    "schema_version",
    "hardware_uuid",
)
ALLOWED_LEAVES = (str, int, float, bool, type(None))
VIEW_MODULES = (view.__name__, check_history.__name__)


def leaves(value, path="ctx"):
    """Cada hoja del contexto; falla si hay algo que no sea un DTO de la vista o un tipo simple."""
    if isinstance(value, ALLOWED_LEAVES):
        yield path, value
    elif dataclasses.is_dataclass(value) and not isinstance(value, type):
        assert type(value).__module__ in VIEW_MODULES, f"{path}: {type(value)}"
        for f in dataclasses.fields(value):
            yield from leaves(getattr(value, f.name), f"{path}.{f.name}")
    elif isinstance(value, dict):
        for k, v in value.items():
            assert isinstance(k, str), path
            yield from leaves(v, f"{path}[{k!r}]")
    elif isinstance(value, list | tuple):
        for i, v in enumerate(value):
            yield from leaves(v, f"{path}[{i}]")
    else:
        raise AssertionError(f"{path}: {type(value)} no es un DTO de la vista")


def assert_clean(context: dict) -> None:
    for name, value in context.items():
        if name in COMMON_KEYS:
            continue
        for path, leaf in leaves(value, name):
            assert not isinstance(leaf, str) or not any(s in leaf for s in LEAKS), path


@pytest.fixture
def full_workspace(agents):
    admit(agents, revoked_reason=REVOCATION_REASON)
    contact(agents)
    record(
        agents,
        "endpoint",
        [
            result(LAPTOP, CheckStatus.PASS, reason=f"{SECRET_REASON} {LAPTOP_UUID}"),
            result("app-facturas", CheckStatus.FAIL, "HOST-002", reason=SECRET_REASON),
        ],
    )
    record(agents, "github", [result("app-facturas", CheckStatus.UNKNOWN, "GH-1", SECRET_REASON)])
    approve(agents)
    return agents


@pytest.mark.parametrize(
    "path",
    [
        "/inventario",
        "/inventario?tipo=laptop",
        "/inventario/alta",
        f"/inventario/alta?tipo=laptop&id={LAPTOP}&hostname={LAPTOP_HOST}",
        f"/inventario/activos/{LAPTOP}",
        "/inventario/activos/app-facturas?editar=1",
        "/inventario/activos/db-facturas?baja=1",
        "/inventario/activos/lap-personal?baja=1",
    ],
)
def test_el_contexto_solo_lleva_dtos_y_nada_crudo(ui, full_workspace, path):
    assert ui.get(path).status_code == 200

    assert_clean(ui.context)


@pytest.mark.parametrize(
    ("path", "data"),
    [
        ("/inventario/alta", PROVIDER),
        (writes(LAPTOP, "editar"), {"asset_owner": ""}),
        (writes("db-facturas", "baja"), {"confirmar": "db-facturas"}),
    ],
)
def test_los_contextos_422_tampoco_llevan_nada_crudo(ui, full_workspace, path, data):
    assert ui.post(path, data).status_code == 422

    assert_clean(ui.context)
    assert SECRET_REASON not in repr(ui.context["form_errors"])


def test_el_detalle_solo_lleva_el_codigo_del_motivo(ui, full_workspace):
    ui.get("/inventario/activos/app-facturas")

    rows = [row for r in ui.context["detail"].results for row in r.rows]
    assert {(r.check_id, r.reason_code) for r in rows} == {
        ("HOST-002", "some_code"),
        ("GH-1", "some_code"),
    }
    assert SECRET_REASON not in repr(ui.context)


def test_un_activo_excluido_del_alcance_no_aparece_como_desplegado_sin_alta(ui, agents):
    admit(agents)
    contact(agents)

    rows = reconciliation(ui)

    assert "lap-personal" not in rows
    assert SERVER in rows  # lo desplegado e incluido sigue señalándose
