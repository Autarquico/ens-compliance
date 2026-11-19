"""Página de documentos: filas, impedimentos, ficha y entrada hostil. Solo lectura."""

import html
import logging
import re
from datetime import timedelta
from pathlib import Path

import pytest
import yaml

from ens_evidence.models.enums import Role
from ens_evidence.orgdocs import (
    ANSWERS_KEY,
    DocumentState,
    all_statuses,
    approve_document,
    build_documents,
    document_names,
)
from ens_evidence.orgdocs.build import BuildReport, BuildStatus, DocumentResult
from ens_evidence.orgdocs.keys import document_key, meta_key
from ens_evidence.scope import approve_scope
from ens_evidence.ui.roles import ROLES_KEY, Grant, Permissions, RolesStatus, dump_roles
from ens_evidence.ui.store import UiStore
from ens_evidence.ui.views import documents as view
from ens_evidence.ui.views import status as status_view
from ens_evidence.ui.wizard.base import StepRejected
from ens_evidence.workspace import NotFound

from .conftest import NOW, ORIGIN, Session, mint, snapshot_of, ui_client
from .test_wizard_docs import ANSWERS, MANAGER

POLICY, RULES, PROCEDURES = "politica-seguridad", "normativa-seguridad", "procedimientos-seguridad"
EDITOR = Permissions("ana@example.com", RolesStatus.OK, True, frozenset(Role))
READER = Permissions("ana@example.com", RolesStatus.OK, False, frozenset(Role))
NO_ROLE = Permissions("ana@example.com", RolesStatus.OK, True, frozenset())
WRITES = {"write_text", "write_bytes", "create_bytes"}


def write_answers(store, **changes) -> None:
    answers = {**ANSWERS, **changes}
    store.write_text(ANSWERS_KEY, yaml.safe_dump(answers, allow_unicode=True))


@pytest.fixture
def ready(example_store):
    """Alcance aprobado, respuestas completas y los seis documentos generados."""
    approve_scope(example_store, "Ana Ruiz", Role.SYSTEM_OWNER, NOW)
    write_answers(example_store)
    build_documents(example_store)
    return example_store


def unlink(store, key: str) -> None:
    (Path(store._root) / key).unlink()


def forget(store, name: str) -> None:
    unlink(store, document_key(name))
    unlink(store, meta_key(name))


def page_of(store, permissions=EDITOR):
    return view.build_page(UiStore(store), NOW, permissions)


def row_of(page, name):
    return next(r for r in page.rows if r.name == name)


class Spy:
    """Un almacén que apunta cada llamada (método y argumentos) y delega en el real."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.calls: list[tuple[str, tuple]] = []
        self.overrides: dict = {}

    def __getattr__(self, name):
        attr = getattr(self._inner, name)
        if not callable(attr):
            return attr

        def call(*args, **kwargs):
            self.calls.append((name, args))
            if (name, args) in self.overrides:
                result = self.overrides[(name, args)]
                if isinstance(result, Exception):
                    raise result
                return result
            return attr(*args, **kwargs)

        return call

    def writes(self) -> list[tuple[str, tuple]]:
        return [c for c in self.calls if c[0] in WRITES]


@pytest.fixture
def spied(config, ready, key):
    spy = Spy(ready)
    return spy, ui_client(config, spy, key)


def get(client, token, path):
    return client.get(path, headers={"Cf-Access-Jwt-Assertion": token}, follow_redirects=False)


# --- escenario 1: estado con fuente y aprobación ------------------------------------------------


def test_scenario_1_estado_con_fuente_y_aprobacion(session, ready):
    approve_document(ready, POLICY, MANAGER, Role.MANAGEMENT, NOW)
    forget(ready, PROCEDURES)

    page = session.get("/documentos")

    assert page.status_code == 200
    dto = page_of(ready)
    policy, rules, procedures = (row_of(dto, n) for n in (POLICY, RULES, PROCEDURES))
    assert policy.state == "APPROVED" and policy.approval.approved_by == MANAGER
    assert policy.approval.role == "management" and policy.source.template_id == POLICY
    assert rules.state == "GENERATED" and rules.approval is None and rules.tone == "warn"
    assert rules.approver.role == "management"
    assert procedures.state == "NOT_GENERATED" and procedures.source is None
    assert procedures.approval is None
    assert MANAGER in page.text and policy.source.document_sha256 in page.text
    assert policy.source.catalog_consolidation_date in page.text
    assert policy.source.catalog_sha256 in page.text
    assert policy.source.articulado_sha256 in page.text
    assert "Aprobado" in page.text and "Sin generar" in page.text
    assert "Generado, pendiente de aprobar" in page.text
    assert f'href="/documentos/{POLICY}"' in page.text


def test_un_documento_sin_aprobar_nunca_es_favorable_aunque_tenga_historial(ready):
    approve_document(ready, POLICY, MANAGER, Role.MANAGEMENT, NOW)
    write_answers(ready, organizacion="Otra organización distinta, S.L.")

    policy = row_of(page_of(ready), POLICY)

    assert policy.state == "OBSOLETE" and policy.tone == "warn"
    assert policy.approval is None
    assert policy.last_approval is not None and policy.last_approval.approved_by == MANAGER


def test_una_sola_llamada_a_all_statuses_por_peticion(ready, monkeypatch):
    calls = []

    def counted(store):
        calls.append(1)
        return all_statuses(store)

    monkeypatch.setattr(view, "all_statuses", counted)

    page_of(ready)

    assert len(calls) == 1


def test_lo_que_falta_de_cada_documento_va_enlazado_a_su_bloque(example_store):
    approve_scope(example_store, "Ana Ruiz", Role.SYSTEM_OWNER, NOW)
    write_answers(example_store, uso_equipos=None)

    dto = page_of(example_store)

    groups = row_of(dto, "normativa-seguridad").missing
    assert [g.href for g in groups] == ["/asistente/documentos-normativa"]
    assert row_of(dto, POLICY).missing == ()


# --- escenario 2 (GET): impedimentos con enlaces ------------------------------------------------


def test_scenario_2_sin_boton_y_con_motivos_enlazados(session, example_store):
    incidents = ("incidentes_deteccion", "incidentes_niveles")
    write_answers(example_store, **dict.fromkeys(incidents))

    page = session.get("/documentos")

    assert page.status_code == 200
    assert view.SCOPE_NOT_APPROVED in page.text
    assert 'href="/asistente/aprobacion-alcance"' in page.text
    assert 'href="/asistente/documentos-incidentes"' in page.text
    assert 'action="/documentos/generar"' not in page.text
    dto = page_of(example_store)
    assert dto.can_generate is False and view.SCOPE_NOT_APPROVED in dto.blockers
    incident_group = next(g for g in dto.issue_groups if g.slug == "documentos-incidentes")
    assert (
        len(incident_group.items) == 2 and incident_group.href == "/asistente/documentos-incidentes"
    )


def test_generation_blockers_da_todos_los_motivos_a_la_vez(example_store):
    write_answers(example_store, uso_equipos=None, organizacion="n/a")
    report = view._report(UiStore(example_store))

    blockers = view.generation_blockers(UiStore(example_store), report)

    assert blockers.reasons == (view.SCOPE_NOT_APPROVED, view.INVALID_ANSWERS)
    slugs = {g.slug for g in blockers.groups}
    assert {"documentos-normativa", "documentos-organizacion"} <= slugs
    assert all(g.href == f"/asistente/{g.slug}" for g in blockers.groups if g.slug)
    assert not blockers.clear


def test_generation_blockers_cuestionario_sin_empezar(example_store):
    approve_scope(example_store, "Ana Ruiz", Role.SYSTEM_OWNER, NOW)

    page = page_of(example_store)

    assert page.blockers == (view.NOT_STARTED,)
    assert page.answers_started is False and page.can_generate is False
    assert page.issue_groups  # lo que habría que responder, por bloque


def test_generation_blockers_vacio_con_todo_listo(ready):
    blockers = view.generation_blockers(UiStore(ready), view._report(UiStore(ready)))

    assert blockers.clear and blockers.groups == ()


def test_sin_permiso_de_edicion_no_se_ofrece_generar(ready):
    assert page_of(ready, EDITOR).can_generate is True
    assert page_of(ready, READER).can_generate is False


# --- escenario 4 (GET): aviso de caducidad antes de enviar --------------------------------------


def test_scenario_4_aviso_de_documento_desactualizado_antes_de_enviar(session, ready):
    approve_document(ready, POLICY, MANAGER, Role.MANAGEMENT, NOW)
    write_answers(ready, organizacion="Otra organización distinta, S.L.")

    page = session.get("/documentos")

    policy = row_of(page_of(ready), POLICY)
    assert policy.state == "OBSOLETE" and policy.can_approve and policy.can_submit
    assert "no está al día" in policy.drift_notice
    assert all(reason in policy.drift_notice for reason in policy.reasons)
    assert "no está al día" in page.text
    assert f'action="/documentos/{POLICY}/aprobar"' in page.text
    assert 'action="/documentos/generar"' in page.text and "Generar los documentos" in page.text


def test_documento_al_dia_no_lleva_aviso(ready):
    rules = row_of(page_of(ready), RULES)

    assert rules.state == "GENERATED" and rules.drift_notice == ""
    assert rules.can_submit is True


def test_sin_el_rol_no_hay_formulario_y_si_aviso_con_enlace(config, ready, key, token):
    ready.write_text(ROLES_KEY, dump_roles({"ana@example.com": Grant(True, frozenset())}))
    client = ui_client(config, ready, key)

    page = get(client, token, "/documentos")

    rules = row_of(page_of(ready, NO_ROLE), RULES)
    assert rules.can_submit is False and rules.role_missing is True
    assert "la dirección" in rules.role_notice
    assert "la dirección" in page.text
    assert "/aprobar" not in page.text
    assert 'href="/asistente/documentos-responsables"' in page.text


def test_quien_solo_lee_no_ve_el_aviso_de_rol_sino_el_de_solo_lectura(config, ready, key, token):
    ready.write_text(ROLES_KEY, dump_roles({"ana@example.com": Grant(False, frozenset())}))
    client = ui_client(config, ready, key)

    page = get(client, token, "/documentos")

    reader_rows = page_of(ready, Permissions("ana@example.com", RolesStatus.OK, False, frozenset()))
    assert all(r.role_notice == "" for r in reader_rows.rows)
    assert "tu cuenta no tiene ese rol" not in page.text
    assert "Solo lectura" in page.text


def test_sin_autoridad_declarada_de_los_procedimientos_se_dice_y_no_hay_formulario(ready):
    write_answers(ready, autoridad_procedimientos=None)

    procedures = row_of(page_of(ready), PROCEDURES)

    assert procedures.approver.role is None and procedures.can_submit is False
    assert procedures.role_notice == view.NO_AUTHORITY
    assert procedures.approver.responsables_href == "/asistente/documentos-responsables"


# --- ficha y D6 -----------------------------------------------------------------------------------


def test_ficha_con_meta_historial_y_texto_escapado(session, ready):
    approve_document(ready, POLICY, MANAGER, Role.MANAGEMENT, NOW)
    text = ready.read_text(document_key(POLICY))

    page = session.get(f"/documentos/{POLICY}")

    assert page.status_code == 200
    detail = view.build_detail(UiStore(ready), POLICY, EDITOR)
    assert detail.text == text and detail.truncated is False
    assert detail.clauses and detail.measures
    assert [h.approved_by for h in detail.history] == [MANAGER]
    assert detail.unreadable == ()


def test_el_html_del_documento_se_muestra_escapado(session, ready):
    text = ready.read_text(document_key(POLICY))
    ready.write_text(document_key(POLICY), "<img src=x onerror=alert(1)>\n" + text)

    page = session.get(f"/documentos/{POLICY}")

    assert page.status_code == 200
    assert "<img src=x" not in page.text and "&lt;img src=x" in page.text


def test_texto_grande_se_recorta_solo_en_la_presentacion(session, ready):
    ready.write_text(document_key(POLICY), "a" * (view.MAX_DOCUMENT_BYTES + 500))

    detail = view.build_detail(UiStore(ready), POLICY, EDITOR)

    assert detail.truncated is True and len(detail.text) == view.MAX_DOCUMENT_BYTES
    assert detail.row.state != "NOT_GENERATED"  # el estado no sale del prefijo
    assert session.get(f"/documentos/{POLICY}").status_code == 200


def test_fichero_binario_no_da_500(session, ready):
    ready.write_bytes(document_key(POLICY), b"\xff\xfe\x00\x01binario")

    page = session.get(f"/documentos/{POLICY}")

    assert page.status_code == 404  # un binario no cuenta como generado
    assert view.build_detail(UiStore(ready), POLICY, EDITOR).text == ""
    assert session.get("/documentos").status_code == 200


def test_binario_entre_dos_lecturas_se_decodifica_con_reemplazo(ready):
    key = document_key(POLICY)
    spy = Spy(ready)
    spy.overrides[("read_bytes", (key, view.MAX_DOCUMENT_BYTES + 1))] = b"ab\xff\x00cd"

    detail = view.build_detail(UiStore(spy), POLICY, EDITOR)

    assert detail.text == "ab�\x00cd" and detail.truncated is False


def test_fichero_que_desaparece_entre_lecturas_no_da_500(config, ready, key, token):
    doc = document_key(POLICY)
    spy = Spy(ready)
    spy.overrides[("read_bytes", (doc, view.MAX_DOCUMENT_BYTES + 1))] = NotFound("desaparecido")
    client = ui_client(config, spy, key)

    page = get(client, token, f"/documentos/{POLICY}")

    assert page.status_code == 200
    assert view.build_detail(UiStore(spy), POLICY, EDITOR).text == ""


def test_documento_conocido_sin_generar_da_404_en_la_ficha_y_el_listado_lo_explica(session, ready):
    forget(ready, PROCEDURES)

    assert session.get(f"/documentos/{PROCEDURES}").status_code == 404
    listing = session.get("/documentos")
    assert listing.status_code == 200
    assert f'href="/documentos/{PROCEDURES}"' not in listing.text
    assert row_of(page_of(ready), PROCEDURES).state == "NOT_GENERATED"


# --- D8: entrada hostil ---------------------------------------------------------------------------


@pytest.mark.parametrize("segment", ["..%2Forg%2Fanswers", "%3Cscript%3E", "no-existe"])
def test_nombre_desconocido_da_404_sin_derivar_ninguna_clave(spied, token, segment):
    spy, client = spied
    spy.calls.clear()

    page = get(client, token, f"/documentos/{segment}")

    assert page.status_code == 404
    keys = [str(a) for _, args in spy.calls for a in args]
    assert not any("script" in k or "no-existe" in k or "answers" in k for k in keys)
    assert spy.writes() == []


def test_la_ficha_de_un_nombre_conocido_solo_lee_claves_de_ese_documento(spied, token):
    spy, client = spied
    spy.calls.clear()

    assert get(client, token, f"/documentos/{POLICY}").status_code == 200

    assert spy.writes() == []


# --- transversales ---


@pytest.mark.parametrize("path", ["/documentos", f"/documentos/{POLICY}"])
def test_la_cabecera_csp_esta_y_la_pagina_nunca_escribe(spied, token, path):
    spy, client = spied
    before = {k: spy.read_bytes(k) for k in spy.list("")}
    spy.calls.clear()

    page = get(client, token, path)

    assert page.status_code == 200
    assert "default-src" in page.headers["content-security-policy"]
    assert spy.writes() == []
    assert {k: spy.read_bytes(k) for k in spy.list("")} == before


def test_hecho_aprobado_es_un_valor_cerrado_que_solo_muestra_un_aviso(session, ready):
    assert "Aprobación registrada" in session.get("/documentos?hecho=aprobado").text
    hostile = session.get("/documentos?hecho=<b>x</b>&creados=politica-seguridad")
    assert hostile.status_code == 200
    assert "Aprobación registrada" not in hostile.text and "<b>x</b>" not in hostile.text


def test_lista_vacia_en_un_workspace_sin_nada(session, example_store):
    page = session.get("/documentos")

    assert page.status_code == 200
    assert {r.state for r in page_of(example_store).rows} == {"NOT_GENERATED"}


def test_generate_outcome_sale_del_build_report():
    report = BuildReport(
        documents=[
            DocumentResult("a", BuildStatus.CREATED, "1" * 64),
            DocumentResult("b", BuildStatus.UPDATED, "2" * 64, edited_by_hand=True),
            DocumentResult("c", BuildStatus.UNCHANGED, "3" * 64),
            DocumentResult("d", BuildStatus.UPDATED, "4" * 64),
        ]
    )

    outcome = view.generate_outcome(report)

    assert outcome.created == (view.OutcomeDocument("a", "1" * 64),)
    assert outcome.updated == (
        view.OutcomeDocument("b", "2" * 64, archived=True),
        view.OutcomeDocument("d", "4" * 64),
    )
    assert outcome.unchanged == (view.OutcomeDocument("c", "3" * 64),)
    assert [d.name for d in outcome.archived] == ["b"]


def test_la_pagina_no_tiene_resultado_de_generacion_en_un_get(ready):
    assert page_of(ready).outcome is None


def test_una_fila_por_documento_de_las_plantillas(ready):
    assert [r.name for r in page_of(ready).rows] == document_names()


def test_la_fase_documentos_de_estado_enlaza_a_la_pagina(example_store, ready):
    phase = status_view._documents(UiStore(ready), NOW, None)
    pending = status_view._documents(UiStore(example_store), NOW, None)

    for shown in (phase, pending):
        assert shown.operate_href == "/documentos"
        assert shown.operate_label == "Ver los documentos"
    assert status_view._scope(UiStore(ready), NOW, None).operate_href == ""


# --- escritura: audit y ayudas -------------------------------------------------------------------

EMAIL = "ana@example.com"
SECURITY_NAME = ANSWERS["resp_seguridad"]
SYSTEM_NAME = ANSWERS["resp_sistema"]


@pytest.fixture
def audit(caplog):
    caplog.set_level(logging.INFO, logger="ens_evidence.ui.audit")
    return caplog


def audit_lines(audit, outcome: str) -> list[str]:
    return [r.message for r in audit.records if f"outcome={outcome}" in r.message]


def all_audit_lines(audit) -> list[str]:
    return [r.message for r in audit.records if r.name == "ens_evidence.ui.audit"]


def grant(store, edit: bool = True, *roles: Role) -> None:
    store.write_text(ROLES_KEY, dump_roles({EMAIL: Grant(edit, frozenset(roles))}))


def approvals_of(store, name: str) -> list[str]:
    return store.list(f"org/docs/approvals/{name}/")


def raw_post(client, token, path: str, data: dict):
    headers = {"Cf-Access-Jwt-Assertion": token, "Origin": ORIGIN, "Sec-Fetch-Site": "same-origin"}
    return client.post(path, data=data, headers=headers, follow_redirects=False)


def approve_form(by: str) -> dict:
    return {"approved_by": by}


# --- escenario 2 (POST): los mismos motivos que el GET, sin escribir ----------------------------


def test_scenario_2_post_directo_devuelve_los_motivos_y_no_escribe(session, example_store, audit):
    incidents = ("incidentes_deteccion", "incidentes_niveles")
    write_answers(example_store, **dict.fromkeys(incidents))
    before = snapshot_of(example_store)

    response = session.post("/documentos/generar", {})

    assert response.status_code == 422
    assert view.SCOPE_NOT_APPROVED in response.text
    assert 'href="/asistente/aprobacion-alcance"' in response.text
    assert 'href="/asistente/documentos-incidentes"' in response.text
    assert "No se ha escrito nada" in response.text
    assert snapshot_of(example_store) == before
    lines = audit_lines(audit, "rejected")
    assert len(lines) == 1 and "step=documentos action=generar" in lines[0]
    assert f"identity={EMAIL}" in lines[0] and "keys=-" in lines[0]
    assert not audit_lines(audit, "saved")


def test_los_motivos_del_post_son_los_del_get(session, example_store):
    write_answers(example_store, incidentes_deteccion=None, incidentes_niveles=None)
    store = UiStore(example_store)
    expected = view._blocker_lines(view.generation_blockers(store, view._report(store)))

    response = session.post("/documentos/generar", {})
    get = session.get("/documentos")

    assert expected and all(line in html.unescape(response.text) for line in expected)
    assert all(reason in html.unescape(get.text) for reason in page_of(example_store).blockers)


def test_post_con_respuestas_invalidas_explica_y_no_escribe(session, example_store, audit):
    approve_scope(example_store, "Ana Ruiz", Role.SYSTEM_OWNER, NOW)
    write_answers(example_store, uso_equipos=None, organizacion="n/a")
    before = snapshot_of(example_store)

    response = session.post("/documentos/generar", {})

    assert response.status_code == 422
    assert view.INVALID_ANSWERS in response.text
    assert snapshot_of(example_store) == before
    assert len(audit_lines(audit, "rejected")) == 1


def test_el_422_lista_todas_las_preguntas_pendientes_igual_que_el_get(session, writable):
    write_answers(writable, **dict.fromkeys(ANSWERS, None))
    get_groups = view.generation_blockers(UiStore(writable), view._report(UiStore(writable))).groups
    assert any(len(g.items) > 3 for g in get_groups)

    response = session.post("/documentos/generar", {})
    listing = session.get("/documentos")

    assert response.status_code == 422
    for g in get_groups:
        for item in g.items:
            line = html.escape(f"{g.label}: {item}", quote=False)
            assert line in response.text or html.escape(line) in response.text
            assert html.escape(item, quote=False) in listing.text


def test_post_con_cuestionario_sin_empezar(session, example_store, audit):
    approve_scope(example_store, "Ana Ruiz", Role.SYSTEM_OWNER, NOW)
    before = snapshot_of(example_store)

    response = session.post("/documentos/generar", {})

    assert response.status_code == 422
    assert view.NOT_STARTED in response.text
    assert 'href="/asistente/documentos-organizacion"' in response.text
    assert snapshot_of(example_store) == before
    assert len(audit_lines(audit, "rejected")) == 1


# --- generar: creados, actualizados, sin cambios, archivados -------------------------------------


@pytest.fixture
def writable(example_store):
    approve_scope(example_store, "Ana Ruiz", Role.SYSTEM_OWNER, NOW)
    write_answers(example_store)
    return example_store


def redirect_path(response) -> str:
    location = response.headers["location"]
    assert re.fullmatch(r"/documentos\?resultado=[A-Za-z0-9_-]{16,}#resultado", location)
    return location.split("#")[0]


def generate_and_show(session):
    """POST generar (303 a /documentos?resultado=<token>#resultado) y el GET de esa redirección."""
    response = session.post("/documentos/generar", {})
    assert response.status_code == 303
    return session.get(redirect_path(response))


def test_generar_crea_los_documentos_y_los_lista_con_su_sha(session, writable, audit):
    page = generate_and_show(session)

    assert page.status_code == 200
    statuses = all_statuses(UiStore(writable))
    assert all(s.state is DocumentState.GENERATED for s in statuses)
    for name in document_names():
        sha = view._meta_of(UiStore(writable), next(s for s in statuses if s.name == name))
        assert sha.document_sha256 in page.text
    assert "Creados:" in page.text
    saved = audit_lines(audit, "saved")
    assert len(saved) == 1
    assert all(document_key(n) in saved[0] and meta_key(n) in saved[0] for n in document_names())


def test_el_resultado_se_ve_una_vez_y_recargar_ni_lo_repite_ni_escribe(session, writable, audit):
    generate_and_show(session)
    before = snapshot_of(writable)
    audit.clear()

    again = session.get("/documentos")

    assert "Resultado de la generación" not in again.text and "Creados:" not in again.text
    assert snapshot_of(writable) == before
    assert audit_lines(audit, "saved") == []


def test_el_resultado_no_va_en_la_url_ni_lo_ve_otra_persona(config, writable, key, token):
    client = ui_client(config, writable, key)
    ana = Session(client, token)
    luis = Session(client, mint(key, email="luis@example.com"))
    redirect = ana.post("/documentos/generar", {})
    path = redirect_path(redirect)
    assert "Creados" not in redirect.headers["location"]

    assert "Resultado de la generación" not in luis.get(path).text  # token ajeno
    assert "Resultado de la generación" not in ana.get("/documentos?resultado=inventado").text
    assert "Resultado de la generación" in ana.get(path).text  # el suyo, retirado ahora
    assert "Resultado de la generación" not in ana.get(path).text


def test_dos_generaciones_de_la_misma_persona_se_ven_cada_una_con_su_redireccion(session, writable):
    first = redirect_path(session.post("/documentos/generar", {}))
    write_answers(writable, organizacion="Otra organización distinta, S.L.")
    second = redirect_path(session.post("/documentos/generar", {}))
    assert first != second

    late = session.get(second)
    early = session.get(first)

    assert "Actualizados:" in late.text and "Creados:" not in late.text
    assert "Creados:" in early.text and "Actualizados:" not in early.text


def test_un_get_sin_token_entre_el_post_y_su_redireccion_no_consume_el_resultado(session, writable):
    path = redirect_path(session.post("/documentos/generar", {}))

    assert "Resultado de la generación" not in session.get("/documentos").text

    assert "Resultado de la generación" in session.get(path).text


def test_un_head_antes_del_get_no_gasta_el_resultado(config, writable, key, token):
    client = ui_client(config, writable, key)
    session = Session(client, token)
    path = redirect_path(session.post("/documentos/generar", {}))

    head = client.head(path, headers={"Cf-Access-Jwt-Assertion": token}, follow_redirects=False)

    assert head.status_code == 200
    assert "Resultado de la generación" in session.get(path).text


def test_generar_otra_vez_deja_todo_sin_cambios_y_no_escribe(session, writable, audit):
    generate_and_show(session)
    before = snapshot_of(writable)
    audit.clear()

    page = generate_and_show(session)

    assert "Sin cambios:" in page.text
    assert "Creados:" not in page.text and "Actualizados:" not in page.text
    assert snapshot_of(writable) == before
    assert "keys=-" in audit_lines(audit, "saved")[0]


def test_generar_tras_cambiar_una_respuesta_actualiza(session, writable, audit):
    generate_and_show(session)
    write_answers(writable, organizacion="Otra organización distinta, S.L.")
    audit.clear()

    page = generate_and_show(session)

    assert "Actualizados:" in page.text
    assert "ediciones manuales" not in page.text
    assert document_key(POLICY) in audit_lines(audit, "saved")[0]


def test_una_edicion_manual_archivada_figura_como_actualizada_con_su_aviso(session, writable):
    build_documents(writable)
    writable.write_text(document_key(RULES), "# editado a mano\n")

    page = generate_and_show(session)

    assert "ediciones manuales" in page.text
    updated = page.text.split("Actualizados:", 1)[1].split("Sin cambios:", 1)[0]
    sha = view._meta_of(
        UiStore(writable), next(s for s in all_statuses(UiStore(writable)) if s.name == RULES)
    ).document_sha256
    assert f'href="/documentos/{RULES}"' in updated and sha in updated
    assert "edición manual archivada" in updated
    assert [k for k in writable.list("org/docs/history/") if RULES in k]


def test_el_sha_del_resultado_es_el_del_informe_no_el_del_meta_releido(session, writable):
    path = redirect_path(session.post("/documentos/generar", {}))
    metas = {n: writable.read_text(meta_key(n)) for n in document_names()}
    for name, meta in metas.items():  # otra generación cambia el meta entre el POST y el GET
        sha = view._read_meta(UiStore(writable), name).document_sha256
        writable.write_text(meta_key(name), meta.replace(sha, "f" * 64))

    page = session.get(path)

    for name in document_names():
        reported = view._read_meta(UiStore(writable), name)
        assert reported.document_sha256 == "f" * 64
        written = metas[name]
        sha = next(
            line.split(": ")[1].strip()
            for line in written.splitlines()
            if line.startswith("document_sha256")
        )
        outcome_part = page.text.split('id="resultado"', 1)[1].split("</section>", 1)[0]
        assert sha in outcome_part
        assert "f" * 64 not in outcome_part


def test_el_resultado_de_generar_sale_del_build_report(writable):
    build_documents(writable)
    writable.write_text(document_key(RULES), "# editado a mano\n")
    write_answers(writable, organizacion="Otra organización distinta, S.L.")
    spy_store = UiStore(writable).recording()

    outcome = view.generate_outcome(build_documents(spy_store))

    assert RULES in [d.name for d in outcome.archived] and RULES in [
        d.name for d in outcome.updated
    ]
    assert POLICY in [d.name for d in outcome.updated + outcome.unchanged]


def test_una_url_manipulada_no_muestra_resultado_de_generacion(session, ready):
    page = session.get("/documentos?creados=politica-seguridad&actualizados=x&archivados=y")

    assert page.status_code == 200
    assert "Creados:" not in page.text and "Actualizados:" not in page.text
    assert "ediciones manuales" not in page.text


def test_la_estanteria_caduca_y_esta_acotada():
    outcome = view.GenerateOutcome((), (), ())
    shelf = view.OutcomeShelf(ttl=timedelta(minutes=5), capacity=2)
    old = shelf.put("a", outcome, NOW)
    assert shelf.take("a", old, NOW + timedelta(minutes=6)) is None
    first = shelf.put("a", outcome, NOW)
    second = shelf.put("a", outcome, NOW)
    third = shelf.put("a", outcome, NOW)
    assert shelf.take("a", first, NOW) is None  # el más antiguo cedió ante el tope
    assert shelf.take("b", second, NOW) is None  # ajeno: no se entrega ni se gasta
    assert shelf.take("a", second, NOW) is outcome
    assert shelf.take("a", second, NOW) is None
    assert shelf.take("a", third, NOW) is outcome


def test_un_fallo_en_la_segunda_escritura_audita_solo_las_claves_escritas(
    config, writable, key, token, audit
):
    first_name = document_names()[0]
    real = writable.write_text
    calls = []

    def flaky(k, text):
        calls.append(k)
        if len(calls) == 2:
            raise OSError("disco lleno")
        real(k, text)

    writable.write_text = flaky
    client = ui_client(config, writable, key)

    with pytest.raises(OSError):
        raw_post(client, token, "/documentos/generar", {})

    failed = audit_lines(audit, "failed")
    assert len(failed) == 1
    assert f"keys={document_key(first_name)}" in failed[0]
    assert "disco lleno" not in failed[0] and meta_key(first_name) not in failed[0]
    assert not audit_lines(audit, "saved")


def test_un_lector_recibe_403_y_no_escribe(config, writable, key, token, audit):
    grant(writable, False)
    session = Session(ui_client(config, writable, key), token)
    before = snapshot_of(writable)

    response = session.post("/documentos/generar", {})

    assert response.status_code == 403
    assert snapshot_of(writable) == before
    assert len(audit_lines(audit, "denied")) == 1
    assert not audit_lines(audit, "saved")


# --- escenario 3: aprobar con el rol que exige el documento --------------------------------------


def test_scenario_3_aprobar_con_management(session, ready, audit):
    grant(ready, True, Role.MANAGEMENT)
    controls_before = {k: ready.read_bytes(k) for k in ready.list("controls/")}
    before = snapshot_of(ready)

    response = session.post(f"/documentos/{POLICY}/aprobar", approve_form(MANAGER))

    assert response.status_code == 303
    assert response.headers["location"] == f"/documentos?hecho=aprobado#doc-{POLICY}"
    new = set(snapshot_of(ready)) - set(before)
    assert len(new) == 1 and next(iter(new)).startswith(f"org/docs/approvals/{POLICY}/")
    line = audit_lines(audit, "saved")[0]
    assert f"identity={EMAIL}" in line and "roles=management" in line
    assert f"keys={next(iter(new))}" in line
    assert {k: ready.read_bytes(k) for k in ready.list("controls/")} == controls_before
    assert {k: v for k, v in snapshot_of(ready).items() if k in before} == before
    assert row_of(page_of(ready), POLICY).state == "APPROVED"


def test_la_auditoria_de_aprobar_no_lleva_el_nombre(session, ready, audit):
    grant(ready, True, Role.MANAGEMENT)

    session.post(f"/documentos/{POLICY}/aprobar", approve_form(MANAGER))

    assert all("Marta" not in line and "approved_by" not in line for line in all_audit_lines(audit))


def test_scenario_4_post_obsoleto_da_422_no_esta_al_dia(session, ready, audit):
    approve_document(ready, POLICY, MANAGER, Role.MANAGEMENT, NOW)
    write_answers(ready, organizacion="Otra organización distinta, S.L.")
    before = snapshot_of(ready)

    response = session.post(f"/documentos/{POLICY}/aprobar", approve_form(MANAGER))

    assert response.status_code == 422
    assert "no está al día" in response.text
    assert snapshot_of(ready) == before
    assert len(audit_lines(audit, "rejected")) == 1 and not audit_lines(audit, "saved")


def test_un_editor_sin_el_rol_recibe_403_con_el_rol_en_la_linea(config, ready, key, token, audit):
    grant(ready, True)
    session = Session(ui_client(config, ready, key), token)
    before = snapshot_of(ready)

    response = session.post(f"/documentos/{POLICY}/aprobar", approve_form(MANAGER))

    assert response.status_code == 403
    assert snapshot_of(ready) == before
    assert "role=management" in audit_lines(audit, "denied")[0]
    assert not audit_lines(audit, "saved")


@pytest.mark.parametrize("by", ["ana@example.com", "", "   "])
def test_un_email_o_un_nombre_vacio_da_422_en_su_campo(session, ready, audit, by):
    before = snapshot_of(ready)

    response = session.post(f"/documentos/{POLICY}/aprobar", approve_form(by))

    assert response.status_code == 422
    assert f'id="err-approved_by~{POLICY}"' in response.text
    assert snapshot_of(ready) == before
    assert len(audit_lines(audit, "rejected")) == 1


def test_el_nombre_que_no_es_el_declarado_se_rechaza_y_se_repinta_lo_escrito(session, ready):
    before = snapshot_of(ready)

    response = session.post(f"/documentos/{POLICY}/aprobar", approve_form("Pedro Otro"))

    assert response.status_code == 422
    assert 'value="Pedro Otro"' in response.text
    assert snapshot_of(ready) == before


def test_un_secreto_en_el_nombre_no_se_repinta(session, ready):
    secret = "AKIAIOSFODNN7EXAMPLE"

    response = session.post(f"/documentos/{POLICY}/aprobar", approve_form(secret))

    assert response.status_code == 422
    assert secret not in response.text


def test_un_secreto_citado_por_el_producto_no_se_devuelve_en_la_linea_de_error(ready):
    secret = "AKIAIOSFODNN7EXAMPLE"
    echoed = f"{secret} no es la persona declarada en responsable"

    assert secret not in view._unechoed(echoed)
    assert view._unechoed("Pedro Otro no es la persona declarada") == (
        "Pedro Otro no es la persona declarada"
    )
    with pytest.raises(StepRejected) as rejected:
        view._approve(UiStore(ready), POLICY, secret, Role.MANAGEMENT, NOW)
    assert all(secret not in line for line in rejected.value.form_errors)


def test_el_campo_de_aprobar_nunca_viene_relleno(session, ready):
    approve_document(ready, POLICY, MANAGER, Role.MANAGEMENT, NOW)
    page = session.get("/documentos").text

    assert 'name="approved_by" value=""' in page
    assert f'value="{MANAGER}"' not in page


@pytest.mark.parametrize(
    ("segment", "decoded"),
    [
        ("%3Cscript%3E", "<script>"),
        ("no-existe", "no-existe"),
        ("a/b", "a/b"),
        ("a%2Fb", "a/b"),
        ("..%2Forg%2Fanswers", "../org/answers"),
        (f"{POLICY}/x", f"{POLICY}/x"),
    ],
)
def test_aprobar_un_documento_desconocido_da_422_y_no_toca_claves_derivadas(
    spied, ready, token, audit, segment, decoded
):
    spy, client = spied
    before = snapshot_of(ready)
    spy.calls.clear()

    response = raw_post(client, token, f"/documentos/{segment}/aprobar", approve_form(MANAGER))

    assert response.status_code == 422
    assert "No reconocemos ese documento" in response.text
    assert spy.writes() == []
    assert snapshot_of(ready) == before
    assert [c for c in spy.calls if any(decoded in str(a) for a in c[1])] == []
    assert "content-security-policy" in response.headers
    [line] = audit_lines(audit, "rejected")
    assert "step=documentos" in line and "action=aprobar" in line and "keys=-" in line
    assert decoded not in line


# --- D7: rol configurable en procedimientos -------------------------------------------------------


def test_d7_con_el_rol_anterior_tras_cambiar_la_autoridad_da_403_sin_escribir(
    config, ready, key, token, audit
):
    grant(ready, True, Role.SECURITY_OFFICER)
    write_answers(ready, autoridad_procedimientos="el responsable del sistema")
    build_documents(ready)
    session = Session(ui_client(config, ready, key), token)
    before = snapshot_of(ready)

    response = session.post(f"/documentos/{PROCEDURES}/aprobar", approve_form(SYSTEM_NAME))

    assert response.status_code == 403
    assert snapshot_of(ready) == before
    assert "role=system_owner" in audit_lines(audit, "denied")[0]
    assert not audit_lines(audit, "saved")


def test_d7_con_el_rol_vigente_funciona_y_lo_audita(config, ready, key, token, audit):
    write_answers(ready, autoridad_procedimientos="el responsable del sistema")
    build_documents(ready)
    grant(ready, True, Role.SYSTEM_OWNER)
    session = Session(ui_client(config, ready, key), token)

    response = session.post(f"/documentos/{PROCEDURES}/aprobar", approve_form(SYSTEM_NAME))

    assert response.status_code == 303
    line = audit_lines(audit, "saved")[0]
    assert f"identity={EMAIL}" in line and "roles=system_owner" in line
    assert f"org/docs/approvals/{PROCEDURES}/" in line
    assert len(approvals_of(ready, PROCEDURES)) == 1


def test_d7_sin_autoridad_declarada_hay_aviso_sin_boton_y_el_post_se_rechaza(session, ready, audit):
    write_answers(ready, autoridad_procedimientos=None)
    before = snapshot_of(ready)

    page = session.get("/documentos")
    response = session.post(f"/documentos/{PROCEDURES}/aprobar", approve_form(SECURITY_NAME))

    assert view.NO_AUTHORITY in page.text
    assert f'action="/documentos/{PROCEDURES}/aprobar"' not in page.text
    assert response.status_code == 422 and view.NO_AUTHORITY in response.text
    assert snapshot_of(ready) == before
    assert len(audit_lines(audit, "rejected")) == 1


# --- escenarios 5 y 6: lector y nombre hostil en el POST de aprobar -------------------------------


def test_un_lector_recibe_403_al_aprobar_y_no_escribe(config, ready, key, token, audit):
    grant(ready, False, Role.MANAGEMENT)
    session = Session(ui_client(config, ready, key), token)
    before = snapshot_of(ready)

    response = session.post(f"/documentos/{POLICY}/aprobar", approve_form(MANAGER))

    assert response.status_code == 403
    assert snapshot_of(ready) == before
    assert len(audit_lines(audit, "denied")) == 1
    assert not audit_lines(audit, "saved")


def test_aprobar_con_un_nombre_de_documento_con_ruta_se_rechaza_y_no_escribe(session, ready, audit):
    before = snapshot_of(ready)

    response = session.post("/documentos/..%2Forg%2Fanswers/aprobar", approve_form(MANAGER))

    assert response.status_code in (404, 422)
    assert snapshot_of(ready) == before
    assert not audit_lines(audit, "saved")


# --- revisión externa r1 --------------------------------------------------------------------------


def test_el_historial_va_del_mas_antiguo_al_mas_reciente(session, ready):
    older, newer = NOW - timedelta(days=2), NOW - timedelta(days=1)
    approve_document(ready, POLICY, MANAGER, Role.MANAGEMENT, older)
    approve_document(ready, POLICY, MANAGER, Role.MANAGEMENT, newer)

    page = session.get(f"/documentos/{POLICY}").text

    older_at, newer_at = view.format_instant(older), view.format_instant(newer)
    assert page.index(older_at) < page.index(newer_at)
    history = view.build_detail(UiStore(ready), POLICY, EDITOR).history
    assert [h.approved_at for h in history] == [older_at, newer_at]


def test_un_documento_sin_generar_con_aprobacion_previa_la_muestra_como_historia(session, ready):
    approve_document(ready, POLICY, MANAGER, Role.MANAGEMENT, NOW)
    unlink(ready, meta_key(POLICY))

    row = row_of(page_of(ready), POLICY)
    listing = session.get("/documentos").text

    assert row.state == "NOT_GENERATED" and row.approval is None
    assert row.last_approval is not None and row.last_approval.approved_by == MANAGER
    cell = listing.split(f'id="doc-{POLICY}"', 1)[1].split("</tr>", 1)[0]
    assert "Sin aprobación vigente" in cell and "Antes:" in cell and MANAGER in cell
    assert "Sin aprobación</span>" not in cell


@pytest.mark.parametrize(
    "content",
    [b"\xff\xfe\x00", b"a: [unclosed\n", b"- una\n- lista\n"],
    ids=["bin", "yaml", "tipo"],
)
def test_un_fichero_de_respuestas_ilegible_se_diagnostica_en_get_y_post(
    session, writable, audit, content
):
    (Path(writable._root) / ANSWERS_KEY).write_bytes(content)
    before = snapshot_of(writable)

    page = session.get("/documentos")
    refused = session.post("/documentos/generar", {})

    assert page.status_code == 200 and refused.status_code == 422
    assert snapshot_of(writable) == before
    for text in (page.text, refused.text):
        assert "org/answers.yaml" in text
        assert "Todavía no has respondido" not in text
    assert "Todavía no se pueden generar" in page.text
    assert not session.get("/documentos").text.count('action="/documentos/generar"')
