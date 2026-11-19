"""Autorización de la UI: cada operación tiene su política, y lo denegado no escribe nada."""

import logging
import re

import pytest
import yaml
from applicability.helpers import LATER, decide_all_undetermined, prepared_workspace, uniform
from assessment.dossier.transition_helpers import docs_world
from matrix.acceptance_helpers import generate_and_approve_documents
from matrix.helpers import SIGNED_AT
from preaudit.test_service import ANTHROPIC, PREPARED_AT  # noqa: F401
from starlette.routing import Route
from starlette.testclient import TestClient

from ens_evidence.applicability import approve_declaration, propose_applicability
from ens_evidence.models.enums import Role
from ens_evidence.orgdocs.contract import PROCEDURES_AUTHORITY_QUESTION
from ens_evidence.preaudit.records import scan_dispositions
from ens_evidence.preaudit.requests import list_requests
from ens_evidence.scope import approve_scope
from ens_evidence.ui import wizard
from ens_evidence.ui.app import create_ui_app
from ens_evidence.ui.authz import (
    COLLECT_CONNECTORS,
    CONNECTOR_FORMS,
    DOSSIER_ACTIONS,
    INCIDENT_ACTIONS,
    INVENTORY_ACTIONS,
    MATRIX_ACTIONS,
    OPERATIONS,
    WIZARD_STEPS,
    ByParam,
    Need,
)
from ens_evidence.ui.roles import ROLES_KEY, Grant, dump_roles
from ens_evidence.ui.routes import ui_routes
from ens_evidence.ui.views import conectores as conectores_view
from ens_evidence.ui.views import dossier as dossier_view
from ens_evidence.ui.views import incidents as incidents_view
from ens_evidence.ui.views import inventory as inventory_view
from ens_evidence.ui.views import matrix as matrix_view
from ens_evidence.ui.views.conectores_catalogo import CATALOGUE
from ens_evidence.ui.views.dossier import DOSSIER_ROUTES
from ens_evidence.ui.views.matrix import MATRIX_ROUTES
from ens_evidence.ui.views.status import STATUS_ROUTES
from ens_evidence.ui.wizard.docs import BLOCK_OF
from ens_evidence.workspace import LocalStore

from .conftest import NOW, ORIGIN, Session, jwks_for, snapshot_of, ui_client
from .test_custody import FORM as CUSTODY_FORM
from .test_custody import URL as CUSTODY_URL
from .test_matrix_actions import ALL_CLAUSES, MEASURE, OWNER, REVIEW
from .test_preaudit_actions import (  # noqa: F401
    REVIEWED_AT,
    audited,
    consent,
    judged,
    prepared,
    stages,
)
from .test_wizard_categorization import add_info, rating_fields
from .test_wizard_docs import ANSWERS, fill_all, form_for

EMAIL = "ana@example.com"
ALL_ROLES = frozenset(Role)
SO, INFO_OWNER, SERVICE_OWNER = Role.SECURITY_OFFICER, Role.INFORMATION_OWNER, Role.SERVICE_OWNER


def grant(store, edit: bool = True, *roles: Role) -> None:
    store.write_text(ROLES_KEY, dump_roles({EMAIL: Grant(edit, frozenset(roles))}))


@pytest.fixture
def audit(caplog):
    caplog.set_level(logging.INFO, logger="ens_evidence.ui.audit")
    return caplog


def audit_lines(audit, outcome: str) -> list[str]:
    return [r.message for r in audit.records if f"outcome={outcome}" in r.message]


# --- (a) la tabla coincide con lo que la app expone ----------------------------------------------


def registered_writes() -> set[tuple[str, str]]:
    return {
        (method, route.path)
        for route in ui_routes()
        if isinstance(route, Route)
        for method in route.methods or ()
        if method not in {"GET", "HEAD"}
    }


def test_la_tabla_clasifica_exactamente_las_rutas_que_escriben():
    assert registered_writes() == set(OPERATIONS)


def test_la_tabla_clasifica_exactamente_los_pasos_del_asistente():
    assert {s.slug for s in wizard.STEPS} == set(WIZARD_STEPS)


def test_la_tabla_clasifica_exactamente_las_acciones_de_cada_vista():
    assert set(matrix_view.ACTIONS) == set(MATRIX_ACTIONS)
    assert {a for a in dossier_view.ACTIONS} == set(DOSSIER_ACTIONS)
    assert set(incidents_view.ACTIONS) == set(INCIDENT_ACTIONS)
    assert set(inventory_view.ACTIONS) == set(INVENTORY_ACTIONS)
    assert set(conectores_view.EDITABLE) == set(CONNECTOR_FORMS)
    assert set(CATALOGUE) == set(COLLECT_CONNECTORS)


def test_toda_ruta_repartida_por_parametro_tiene_su_tabla():
    by_param = {key for key, entry in OPERATIONS.items() if isinstance(entry, ByParam)}
    assert by_param == {
        ("POST", "/asistente/{slug}"),
        ("POST", "/matriz/{medida}/{accion}"),
        ("POST", "/expediente/{dossier}/{accion}"),
        ("POST", "/incidentes/{incidente}/{accion}"),
        ("POST", "/inventario/activos/{activo}/{accion}"),
        ("POST", "/conectores/{conector}"),
        ("POST", "/conectores/{conector}/recoger"),
    }


def test_una_operacion_sin_clasificar_se_deniega_antes_de_escribir(
    config, store, key, token, audit
):
    app = create_ui_app(
        config,
        store,
        lambda: NOW,
        lambda: jwks_for(key),
        routes=[Route("/nueva", _write_probe, methods=["POST"])],
    )
    before = snapshot_of(store)

    response = Session(TestClient(app, base_url=ORIGIN), token).post("/nueva", {})

    assert response.status_code == 403
    assert snapshot_of(store) == before
    assert "reason=unknown_operation" in audit_lines(audit, "denied")[0]


def test_recoger_con_un_conector_fuera_del_catalogo_se_deniega(ui, store, audit):
    grant(store, True, *Role)

    response = attempt(ui, "/conectores/desconocido/recoger")

    assert response.status_code == 403
    assert "reason=unknown_operation" in audit_lines(audit, "denied")[0]


def test_recoger_lo_puede_pedir_quien_edita_para_cada_conector_del_catalogo():
    for name in CATALOGUE:
        assert COLLECT_CONNECTORS[name] is Need.EDIT


async def _write_probe(request):
    request.app.state.store.write_text("scope/x.yaml", "a: 1\n")
    raise AssertionError("no debería llegar al handler")


# --- (b) matriz por operación --------------------------------------------------------------------


def concrete_operations():
    """(método, ruta concreta, requisito) de cada operación de la tabla."""
    for (method, template), entry in OPERATIONS.items():
        if isinstance(entry, ByParam):
            for value, requirement in entry.policies.items():
                path = template.replace("{" + entry.param + "}", value)
                yield method, re.sub(r"\{\w+\}", "x" * 64, path), requirement
        else:
            yield method, template, entry


OPERATION_CASES = list(concrete_operations())
FIXED_ROLE_CASES = [c for c in OPERATION_CASES if isinstance(c[2], Role)]
IDS = [f"{m} {p[:40]}" for m, p, _ in OPERATION_CASES]


@pytest.fixture
def ui(config, store, example_store, key, token) -> Session:
    return Session(ui_client(config, store, key), token)


def attempt(ui: Session, path: str, *, htmx: bool = False):
    return ui.post(path, {}, htmx=htmx)


@pytest.mark.parametrize(("method", "path", "requirement"), OPERATION_CASES, ids=IDS)
def test_quien_solo_lee_no_puede_hacer_ninguna_operacion(
    ui, store, audit, method, path, requirement
):
    grant(store, False, *Role)
    before = snapshot_of(store)

    response = attempt(ui, path)

    assert response.status_code == 403
    assert snapshot_of(store) == before
    [line] = audit_lines(audit, "denied")
    assert f"identity={EMAIL}" in line and "reason=no_edit" in line


@pytest.mark.parametrize(("method", "path", "requirement"), OPERATION_CASES, ids=IDS)
def test_con_un_fichero_de_roles_invalido_nada_se_escribe(
    ui, store, audit, method, path, requirement
):
    store.write_text(ROLES_KEY, "users: [roto")
    before = snapshot_of(store)

    response = attempt(ui, path)

    assert response.status_code == 403
    assert snapshot_of(store) == before
    assert len(audit_lines(audit, "denied")) == 1


@pytest.mark.parametrize(
    ("method", "path", "requirement"),
    FIXED_ROLE_CASES,
    ids=[f"{m} {p[:40]}" for m, p, _ in FIXED_ROLE_CASES],
)
def test_editor_sin_rol_o_con_otro_rol_no_hace_lo_que_exige_un_rol(
    ui, store, audit, method, path, requirement
):
    for roles in ((), ALL_ROLES - {requirement}):
        grant(store, True, *roles)
        before = snapshot_of(store)

        response = attempt(ui, path)

        assert response.status_code == 403
        assert snapshot_of(store) == before
    lines = audit_lines(audit, "denied")
    assert len(lines) == 2
    assert all(
        "reason=missing_role" in line and f"role={requirement.value}" in line for line in lines
    )


@pytest.mark.parametrize(("method", "path", "requirement"), OPERATION_CASES, ids=IDS)
def test_con_lo_que_pide_la_politica_la_peticion_llega_al_handler(
    ui, store, audit, method, path, requirement
):
    roles = (requirement,) if isinstance(requirement, Role) else ()
    assert requirement is not Need.CHOSEN or roles == ()
    grant(store, True, *roles)

    attempt(ui, path)

    assert audit_lines(audit, "denied") == []


def test_la_denegacion_por_htmx_es_403(ui, store, audit):
    grant(store, False)
    before = snapshot_of(store)

    response = ui.post("/asistente/sistema", {"name": "x"}, htmx=True)

    assert response.status_code == 403
    assert "No se ha guardado nada" in response.text
    assert snapshot_of(store) == before
    assert len(audit_lines(audit, "denied")) == 1


def test_la_denegacion_explica_que_rol_falta_y_como_asignarlo(ui, store):
    grant(store, True)

    page = ui.post("/preauditoria/enviar", {}).text

    assert "ens-evidence ui roles grant" in page and "--role security_officer" in page
    assert "Responsable de la seguridad" in page


# --- (c) leer nunca escribe ----------------------------------------------------------------------


@pytest.fixture
def matrix_world(tmp_path):
    store = prepared_workspace(tmp_path / "signed", uniform("MEDIO"))
    propose_applicability(store, LATER)
    decide_all_undetermined(store)
    approve_declaration(store, "Luis Pardo", SIGNED_AT)
    generate_and_approve_documents(store)
    grant(store, True, *Role)
    return store


@pytest.fixture
def matrix_ui(config, matrix_world, key, token) -> Session:
    app = create_ui_app(
        config, matrix_world, lambda: NOW, lambda: jwks_for(key), routes=[*MATRIX_ROUTES]
    )
    return Session(TestClient(app, base_url=ORIGIN), token)


def drop_last_entry(store) -> str:
    document = yaml.safe_load(store.read_text("controls/matrix.yaml"))
    dropped = document["entries"].pop()["id"]
    store.write_text("controls/matrix.yaml", yaml.safe_dump(document, sort_keys=False))
    return dropped


@pytest.mark.parametrize("edit", [True, False])
def test_leer_la_matriz_ausente_no_escribe(matrix_ui, matrix_world, edit):
    grant(matrix_world, edit, *Role)
    before = snapshot_of(matrix_world)

    for method in ("get", "head"):
        for path in ("/matriz", f"/matriz/{MEASURE}"):
            assert getattr(matrix_ui._client, method)(
                path, headers={"Cf-Access-Jwt-Assertion": matrix_ui._token}
            ).status_code in (200, 405)

    assert snapshot_of(matrix_world) == before
    assert not matrix_world.exists("controls/matrix.yaml")


@pytest.mark.parametrize("edit", [True, False])
def test_leer_una_matriz_con_entradas_pendientes_no_las_completa(matrix_ui, matrix_world, edit):
    matrix_ui.post("/matriz/iniciar", {})
    missing = drop_last_entry(matrix_world)
    grant(matrix_world, edit, *Role)
    before = snapshot_of(matrix_world)

    list_page = matrix_ui.get("/matriz")
    measure_page = matrix_ui.get(f"/matriz/{missing}")

    assert list_page.status_code == measure_page.status_code == 200
    assert snapshot_of(matrix_world) == before


def test_iniciar_solo_lo_hace_quien_edita_y_lo_audita(matrix_ui, matrix_world, audit):
    grant(matrix_world, False)
    assert matrix_ui.post("/matriz/iniciar", {}).status_code == 403
    assert not matrix_world.exists("controls/matrix.yaml")

    grant(matrix_world, True)
    assert matrix_ui.post("/matriz/iniciar", {}).status_code == 303

    assert matrix_world.exists("controls/matrix.yaml")
    assert any("action=iniciar" in m for m in audit_lines(audit, "saved"))


# --- rol elegido en el formulario ---------------------------------------------------------------


def prepare_measure(ui: Session) -> None:
    ui.post("/matriz/iniciar", {})
    assert ui.post(f"/matriz/{MEASURE}/responsable", OWNER).status_code == 303
    link = {"kind": "document", "target": "politica-seguridad", "covers": ALL_CLAUSES}
    assert ui.post(f"/matriz/{MEASURE}/enlazar", link).status_code == 303


def test_revisar_con_un_rol_no_asignado_no_escribe(matrix_ui, matrix_world, audit):
    prepare_measure(matrix_ui)
    grant(matrix_world, True, SO)
    before = snapshot_of(matrix_world)

    response = matrix_ui.post(f"/matriz/{MEASURE}/revisar", REVIEW)  # system_administrator

    assert response.status_code == 403
    assert snapshot_of(matrix_world) == before
    [line] = audit_lines(audit, "denied")
    assert "role=system_administrator" in line


def test_revisar_con_el_rol_asignado_funciona_y_lo_audita(matrix_ui, matrix_world, audit):
    prepare_measure(matrix_ui)
    grant(matrix_world, True, Role.SYSTEM_ADMINISTRATOR)

    response = matrix_ui.post(f"/matriz/{MEASURE}/revisar", REVIEW)

    assert response.status_code == 303
    [line] = [m for m in audit_lines(audit, "saved") if "action=revisar" in m]
    assert "roles=system_administrator" in line


def test_el_formulario_de_revision_solo_ofrece_los_roles_asignados(matrix_ui, matrix_world):
    prepare_measure(matrix_ui)
    grant(matrix_world, True, Role.SYSTEM_ADMINISTRATOR)

    page = matrix_ui.get(f"/matriz/{MEASURE}").text
    form = page.split('action="/matriz/org.1/revisar"')[1].split("</form>")[0]

    assert 'value="system_administrator"' in form
    assert 'value="security_officer"' not in form and 'value="management"' not in form


@pytest.fixture
def dossier_ui(config, tmp_path, key, token, monkeypatch):
    docs_world(tmp_path / "ws")
    store = LocalStore(tmp_path / "ws")
    grant(store, True, *Role)
    calls = []
    monkeypatch.setattr(dossier_view, "record_transition", lambda *a, **k: calls.append((a, k)))
    app = create_ui_app(
        config, store, lambda: NOW, lambda: jwks_for(key), routes=[*STATUS_ROUTES, *DOSSIER_ROUTES]
    )
    ui = Session(TestClient(app, base_url=ORIGIN), token)
    generated = ui.post("/expediente/generar", {})
    assert generated.status_code == 303
    return ui, store, generated.headers["location"].split("?")[0].rsplit("/", 1)[1], calls


def test_autoevaluar_con_un_rol_no_asignado_no_registra_nada(dossier_ui, audit):
    ui, store, dossier, calls = dossier_ui
    grant(store, True, SO)
    before = snapshot_of(store)

    response = ui.post(
        f"/expediente/{dossier}/self-assess", {"by": "Ana", "role": "system_administrator"}
    )

    assert response.status_code == 403
    assert calls == [] and snapshot_of(store) == before
    assert "role=system_administrator" in audit_lines(audit, "denied")[0]


def test_autoevaluar_con_el_rol_asignado_lo_registra_con_ese_rol(dossier_ui, audit):
    ui, store, dossier, calls = dossier_ui
    grant(store, True, Role.SYSTEM_ADMINISTRATOR)

    response = ui.post(
        f"/expediente/{dossier}/self-assess", {"by": "Ana", "role": "system_administrator"}
    )

    assert response.status_code == 303
    [(_, kwargs)] = calls
    assert kwargs["role"] is Role.SYSTEM_ADMINISTRATOR
    assert "roles=system_administrator" in [m for m in audit_lines(audit, "saved")][-1]


def test_custodios_con_un_rol_no_asignado_no_escriben(ui, store, audit):
    grant(store, True)
    before = snapshot_of(store)

    response = ui.post(CUSTODY_URL, CUSTODY_FORM)  # security_officer

    assert response.status_code == 403
    assert snapshot_of(store) == before
    assert "role=security_officer" in audit_lines(audit, "denied")[0]


def test_custodios_con_el_rol_asignado_se_registran_y_se_audita_el_rol(ui, store, audit):
    grant(store, True, SO)

    response = ui.post(CUSTODY_URL, CUSTODY_FORM)

    assert response.status_code == 303
    assert "roles=security_officer" in audit_lines(audit, "saved")[-1]


# --- (d) valoraciones con roles mixtos -----------------------------------------------------------


@pytest.fixture
def valuations(ui, store, example_store):
    approve_scope(example_store, "Ana Ruiz", Role.SYSTEM_OWNER, NOW)
    add_info(ui)
    form = {
        **rating_fields("information", "datos", "ALTO"),
        **rating_fields("service", "svc-facturacion", "BAJO"),
        "valued_by~information~datos": "Ana Ruiz",
        "valued_by~service~svc-facturacion": "Ana Ruiz",
    }
    return ui, store, form


def test_firmar_dos_tipos_con_un_solo_rol_no_escribe_nada(valuations, audit):
    ui, store, form = valuations
    grant(store, True, INFO_OWNER)
    before = snapshot_of(store)

    response = ui.post("/asistente/valoraciones", form)

    assert response.status_code == 403
    assert snapshot_of(store) == before
    assert "role=service_owner" in audit_lines(audit, "denied")[0]


def test_firmar_dos_tipos_con_los_dos_roles_funciona_y_audita_ambos(valuations, audit):
    ui, store, form = valuations
    grant(store, True, INFO_OWNER, SERVICE_OWNER)

    assert ui.post("/asistente/valoraciones", form).status_code == 303

    line = audit_lines(audit, "saved")[-1]
    assert "roles=information_owner,service_owner" in line


def test_firmar_solo_la_informacion_basta_con_su_rol(valuations):
    ui, store, form = valuations
    grant(store, True, INFO_OWNER)
    only_info = {k: v for k, v in form.items() if "service" not in k}

    assert ui.post("/asistente/valoraciones", only_info).status_code == 303


def test_valoraciones_anuncia_el_rol_que_falta_e_inactiva_solo_esa_firma(valuations):
    ui, store, _ = valuations
    grant(store, True, INFO_OWNER)

    page = ui.get("/asistente/valoraciones").text

    assert page.count("Necesitas el rol") == 1
    assert "--role service_owner" in page
    assert 'name="valued_by~service~svc-facturacion" disabled' in page
    assert 'name="valued_by~information~datos" disabled' not in page
    assert 'name="rating~service~svc-facturacion~C~level"' in page
    assert 'rating~service~svc-facturacion~C~level" disabled' not in page


def test_valoraciones_con_los_dos_roles_no_anuncia_nada(valuations):
    ui, store, _ = valuations
    grant(store, True, INFO_OWNER, SERVICE_OWNER)

    page = ui.get("/asistente/valoraciones").text

    assert "Necesitas el rol" not in page
    assert '~service~svc-facturacion" disabled' not in page


# --- (e) pre-auditoría ---------------------------------------------------------------------------


@pytest.mark.parametrize("roles", [(), (Role.MANAGEMENT,)])
def test_enviar_sin_responsable_de_la_seguridad_no_crea_peticion(prepared, audit, roles):  # noqa: F811
    ui, store, _, done = prepared
    grant(store, True, *roles)
    before = snapshot_of(store)

    response = ui.post("/preauditoria/enviar", consent(done))

    assert response.status_code == 403
    assert list_requests(store, PREPARED_AT) == []
    assert snapshot_of(store) == before
    assert "role=security_officer" in audit_lines(audit, "denied")[0]


def test_enviar_con_el_rol_crea_la_peticion_y_audita_el_rol(prepared, audit):  # noqa: F811
    ui, store, _, done = prepared
    grant(store, True, SO)

    assert ui.post("/preauditoria/enviar", consent(done)).status_code == 303

    assert len(list_requests(store, PREPARED_AT)) == 1
    assert "roles=security_officer" in audit_lines(audit, "saved")[-1]


def decision(dossier):
    return {
        "dossier": dossier,
        "decision": "dismissed",
        "reason": "Ya no está en vigor.",
        "by": "Ana",
    }


def test_decidir_sin_responsable_de_la_seguridad_no_crea_disposicion(judged, audit):  # noqa: F811
    ui, store, dossier, finding, _ = judged
    grant(store, True)
    before = snapshot_of(store)

    response = ui.post(f"/preauditoria/hallazgos/{finding.id}", decision(dossier))

    assert response.status_code == 403
    assert len(scan_dispositions(store, now=REVIEWED_AT).records) == 0
    assert snapshot_of(store) == before


def test_decidir_con_el_rol_crea_la_disposicion(judged, audit):  # noqa: F811
    ui, store, dossier, finding, _ = judged
    grant(store, True, SO)

    response = ui.post(f"/preauditoria/hallazgos/{finding.id}", decision(dossier))

    assert response.status_code == 303
    assert len(scan_dispositions(store, now=REVIEWED_AT).records) == 1


# --- (f) aprobar documentos ----------------------------------------------------------------------


@pytest.fixture
def documents(ui, store, example_store):
    approve_scope(example_store, "Ana Ruiz", Role.SYSTEM_OWNER, NOW)
    return ui, store


def test_aprobar_sin_firmante_declarado_se_bloquea_aunque_haya_todos_los_roles(documents):
    ui, store = documents
    before = snapshot_of(store)

    response = ui.post(
        "/asistente/documentos-aprobar",
        {"accion": "aprobar", "documento": "procedimientos-seguridad", "approved_by": "Ana Ruiz"},
    )

    assert response.status_code == 422
    assert "Antes de aprobar este documento" in response.text
    assert snapshot_of(store) == before


def test_aprobar_un_documento_sin_el_rol_de_su_ficha_no_escribe(documents, audit):
    ui, store = documents
    fill_all(ui)
    ui.post("/asistente/documentos-generar", {"accion": "generar"})
    grant(store, True)
    before = snapshot_of(store)

    response = ui.post(
        "/asistente/documentos-aprobar",
        {
            "accion": "aprobar",
            "documento": "politica-seguridad",
            "approved_by": "Marta Ibáñez Soler",
        },
    )

    assert response.status_code == 403
    assert snapshot_of(store) == before
    assert "role=management" in audit_lines(audit, "denied")[0]


def test_aprobar_con_el_rol_de_su_ficha_funciona_y_lo_audita(documents, audit):
    ui, store = documents
    fill_all(ui)
    ui.post("/asistente/documentos-generar", {"accion": "generar"})
    grant(store, True, Role.MANAGEMENT)

    response = ui.post(
        "/asistente/documentos-aprobar",
        {
            "accion": "aprobar",
            "documento": "politica-seguridad",
            "approved_by": "Marta Ibáñez Soler",
        },
    )

    assert response.status_code == 303
    assert "roles=management" in audit_lines(audit, "saved")[-1]


def test_la_pagina_de_aprobacion_deshabilita_lo_que_el_usuario_no_puede_firmar(documents):
    ui, store = documents
    fill_all(ui)
    ui.post("/asistente/documentos-generar", {"accion": "generar"})
    grant(store, True)

    page = ui.get("/asistente/documentos-aprobar").text

    assert "disabled" in page


def test_aprobar_en_la_pagina_de_documentos_sin_el_rol_de_su_ficha_no_escribe(documents, audit):
    ui, store = documents
    fill_all(ui)
    ui.post("/documentos/generar", {"x": "1"})
    grant(store, True)
    before = snapshot_of(store)

    response = ui.post("/documentos/politica-seguridad/aprobar", {"approved_by": "Marta Ibáñez"})

    assert response.status_code == 403
    assert snapshot_of(store) == before
    assert "role=management" in audit_lines(audit, "denied")[0]


def test_aprobar_en_la_pagina_de_documentos_con_el_rol_de_su_ficha_funciona(documents, audit):
    ui, store = documents
    fill_all(ui)
    ui.post("/documentos/generar", {"x": "1"})
    grant(store, True, Role.MANAGEMENT)

    response = ui.post(
        "/documentos/politica-seguridad/aprobar", {"approved_by": "Marta Ibáñez Soler"}
    )

    assert response.status_code == 303
    assert "roles=management" in audit_lines(audit, "saved")[-1]


def test_los_pasos_de_aprobacion_con_rol_fijo_deniegan_sin_el_rol(ui, store, audit):
    grant(store, True, Role.SYSTEM_OWNER)

    for step, role in (
        ("aprobacion-categorizacion", SO),
        ("aprobacion-riesgos", SO),
        ("aprobacion-aplicabilidad", SO),
    ):
        assert ui.post(f"/asistente/{step}", {"accion": "aprobar"}).status_code == 403
        assert f"role={role.value}" in audit_lines(audit, "denied")[-1]

    grant(store, True, SO)
    assert ui.post("/asistente/aprobacion-alcance", {"accion": "aprobar"}).status_code == 403
    assert "role=system_owner" in audit_lines(audit, "denied")[-1]


def test_cambiar_quien_aprueba_los_procedimientos_exige_la_direccion(documents, audit):
    ui, store = documents
    fill_all(ui)
    slug = BLOCK_OF[PROCEDURES_AUTHORITY_QUESTION].slug
    form = form_for(slug, {**ANSWERS, PROCEDURES_AUTHORITY_QUESTION: "el responsable del sistema"})
    grant(store, True, Role.SYSTEM_OWNER)
    before = snapshot_of(store)

    response = ui.post(f"/asistente/{slug}", form)

    assert response.status_code == 403
    assert "role=management" in audit_lines(audit, "denied")[-1]
    assert snapshot_of(store) == before


def test_la_direccion_si_puede_cambiar_quien_aprueba_los_procedimientos(documents):
    ui, store = documents
    fill_all(ui)
    slug = BLOCK_OF[PROCEDURES_AUTHORITY_QUESTION].slug
    form = form_for(slug, {**ANSWERS, PROCEDURES_AUTHORITY_QUESTION: "el responsable del sistema"})
    grant(store, True, Role.MANAGEMENT)

    assert ui.post(f"/asistente/{slug}", form).status_code == 303


def test_sin_direccion_el_bloque_avisa_y_deja_la_pregunta_de_solo_lectura(documents):
    ui, store = documents
    fill_all(ui)
    slug = BLOCK_OF[PROCEDURES_AUTHORITY_QUESTION].slug
    grant(store, True, Role.SYSTEM_OWNER)

    page = ui.get(f"/asistente/{slug}").text

    assert "ens-evidence ui roles grant" in page and "--role management" in page
    assert f'name="q~{PROCEDURES_AUTHORITY_QUESTION}"' not in page
    assert re.search(rf'<select id="f-{PROCEDURES_AUTHORITY_QUESTION}"[^>]* disabled', page)


def test_sin_direccion_se_guarda_el_resto_del_bloque_si_la_pregunta_no_cambia(documents):
    ui, store = documents
    fill_all(ui)
    slug = BLOCK_OF[PROCEDURES_AUTHORITY_QUESTION].slug
    grant(store, True, Role.SYSTEM_OWNER)

    assert ui.post(f"/asistente/{slug}", form_for(slug, ANSWERS)).status_code == 303


@pytest.mark.parametrize("conector", ["backups", "endpoint", "desconocido"])
def test_un_conector_sin_formulario_se_deniega_antes_del_handler(ui, store, audit, conector):
    grant(store, True, *Role)
    before = snapshot_of(store)

    response = attempt(ui, f"/conectores/{conector}")

    assert response.status_code == 403
    assert snapshot_of(store) == before
    assert "reason=unknown_operation" in audit_lines(audit, "denied")[0]


def test_los_conectores_editables_son_los_seis_con_formulario():
    assert set(CONNECTOR_FORMS) == {
        "github",
        "cloudflare",
        "google-workspace",
        "keystore",
        "passwords",
        "restic",
    }
