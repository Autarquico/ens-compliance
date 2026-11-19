"""Página de documentos: filas, impedimentos, ficha y entrada hostil. Solo lectura."""

import dataclasses
from pathlib import Path

import pytest
import yaml

from ens_evidence.models.enums import Role
from ens_evidence.orgdocs import (
    ANSWERS_KEY,
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
from ens_evidence.workspace import NotFound

from .conftest import NOW, ui_client
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
    assert "/aprobar" not in page.text
    assert 'href="/asistente/documentos-responsables"' in page.text


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

    assert page.status_code == 200
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


def test_documento_conocido_sin_generar_muestra_la_ficha_sin_texto(session, ready):
    forget(ready, PROCEDURES)

    page = session.get(f"/documentos/{PROCEDURES}")

    assert page.status_code == 200
    detail = view.build_detail(UiStore(ready), PROCEDURES, EDITOR)
    assert detail.row.state == "NOT_GENERATED"
    assert detail.text == "" and detail.clauses == () and detail.measures == ()


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
            DocumentResult("a", BuildStatus.CREATED, "0" * 64),
            DocumentResult("b", BuildStatus.UPDATED, "0" * 64, edited_by_hand=True),
            DocumentResult("c", BuildStatus.UNCHANGED, "0" * 64),
        ]
    )

    outcome = view.generate_outcome(report)

    assert dataclasses.astuple(outcome) == (("a",), ("b",), ("c",), ("b",))


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
