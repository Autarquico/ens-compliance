import logging
import re
from pathlib import Path

import pytest
import yaml

from ens_evidence.models.enums import Role
from ens_evidence.orgdocs import (
    ANSWERS_KEY,
    DocumentState,
    all_statuses,
    document_names,
    load_templates,
)
from ens_evidence.scope import approve_scope
from ens_evidence.ui import wizard
from ens_evidence.ui.wizard.docs import BLOCKS, DOC_STEPS

from .conftest import NOW, snapshot_of

ANSWERS = yaml.safe_load(
    (Path(__file__).resolve().parents[1] / "orgdocs" / "fixtures" / "answers.yaml").read_text(
        encoding="utf-8"
    )
)
QUESTIONS = {q.id: q for t in load_templates() for q in t.template.questions}
BLOCK = {b.slug: b for b in BLOCKS}
FORBIDDEN = re.compile(r"certific|garantiza el cumplimiento|cumple el ENS", re.IGNORECASE)
INLINE = re.compile(r"<script(?![^>]*\ssrc=)|<style|\sstyle=|\son[a-z]+=", re.IGNORECASE)
CLI = re.compile(r"ens-evidence|`|docs build|docs init", re.IGNORECASE)
MANAGER, SECURITY = "Marta Ibáñez Soler", "Lucía Navarro Gil"


@pytest.fixture(autouse=True)
def mounted(monkeypatch):
    steps = (*wizard.STEPS, *DOC_STEPS)
    monkeypatch.setattr(wizard, "STEPS", steps)
    monkeypatch.setattr(wizard, "BY_SLUG", {s.slug: s for s in steps})


@pytest.fixture
def approved(example_store):
    approve_scope(example_store, "Ana Ruiz", Role.SYSTEM_OWNER, NOW)
    return example_store


def form_for(slug: str, answers: dict | None = None) -> dict:
    answers = ANSWERS if answers is None else answers
    form: dict = {}
    for qid in BLOCK[slug].question_ids:
        question, value = QUESTIONS[qid], answers.get(qid)
        form[f"q~{qid}"] = "1"
        if isinstance(value, dict):
            form[f"na~{qid}"] = "1"
            form[f"reason~{qid}"] = value["reason"]
        elif isinstance(value, list) and value and isinstance(value[0], dict):
            for spec in question.fields:
                form[f"r~{qid}~{spec.id}"] = [row[spec.id] for row in value]
        elif isinstance(value, list):
            form[f"a~{qid}"] = value
        elif value:
            form[f"a~{qid}"] = value
    return form


def fill_all(session) -> None:
    for block in BLOCKS:
        assert session.post(f"/asistente/{block.slug}", form_for(block.slug)).status_code == 303


def stored(store) -> dict:
    return yaml.safe_load(store.read_text(ANSWERS_KEY))


def test_blocks_cover_every_question_exactly_once():
    ids = [qid for block in BLOCKS for qid in block.question_ids]

    assert sorted(ids) == sorted(QUESTIONS)


def test_steps_expose_blocks_generate_and_approve():
    assert [s.slug for s in DOC_STEPS][-2:] == ["documentos-generar", "documentos-aprobar"]
    assert {s.slug for s in DOC_STEPS[:-2]} == set(BLOCK)


@pytest.mark.parametrize("slug", list(BLOCK))
def test_block_page_shows_clause_and_plain_help(session, approved, slug):
    page = session.get(f"/asistente/{slug}")

    assert page.status_code == 200
    first_id = next(q for q in BLOCK[slug].question_ids if not QUESTIONS[q].required_if)
    assert QUESTIONS[first_id].text in page.text
    assert "Cláusula que lo motiva" in page.text
    assert not INLINE.search(page.text)
    assert not FORBIDDEN.search(page.text)


def test_block_saves_through_set_answer_and_audits(session, approved, caplog):
    caplog.set_level(logging.INFO, logger="ens_evidence.ui.audit")

    response = session.post("/asistente/documentos-normativa", form_for("documentos-normativa"))

    assert response.status_code == 303
    assert stored(approved)["uso_equipos"] == ANSWERS["uso_equipos"]
    assert "step=documentos-normativa" in caplog.text
    assert f"keys={ANSWERS_KEY}" in caplog.text
    assert "Los portátiles" not in caplog.text


def test_saving_the_same_block_twice_writes_nothing_the_second_time(session, approved):
    session.post("/asistente/documentos-normativa", form_for("documentos-normativa"))
    before = snapshot_of(approved)

    response = session.post("/asistente/documentos-normativa", form_for("documentos-normativa"))

    assert response.status_code == 303
    assert "guardado" not in response.headers["location"]
    assert snapshot_of(approved) == before


def test_filler_answers_are_rejected_and_nothing_is_written(session, approved):
    before = snapshot_of(approved)
    form = {"q~organizacion": "1", "a~organizacion": "n/a", "q~mision": "1", "a~mision": "x"}

    response = session.post("/asistente/documentos-organizacion", form)

    assert response.status_code == 422
    assert 'id="err-a~organizacion"' in response.text
    assert snapshot_of(approved) == before


def test_filler_in_a_list_item_names_the_problem(session, approved):
    form = {"q~uso_equipos": "1", "a~uso_equipos": ["Regla concreta de verdad", "pendiente"]}

    response = session.post("/asistente/documentos-normativa", form)

    assert response.status_code == 422
    assert "elemento 2" in response.text
    assert "uso_equipos:" not in response.text


def test_the_error_keeps_what_the_user_typed(session, approved):
    form = {"q~uso_equipos": "1", "a~uso_equipos": ["Regla concreta de verdad", "pendiente"]}

    response = session.post("/asistente/documentos-normativa", form)

    assert 'value="Regla concreta de verdad"' in response.text
    assert 'value="pendiente"' in response.text


def test_text_that_looks_like_a_secret_is_not_echoed(session, approved):
    secret = "AKIAIOSFODNN7EXAMPLE"
    form = {"q~uso_equipos": "1", "a~uso_equipos": [secret, "pendiente"]}

    response = session.post("/asistente/documentos-normativa", form)

    assert response.status_code == 422
    assert secret not in response.text


def test_lists_offer_empty_rows_to_add_and_a_checkbox_to_remove(session, approved):
    session.post(
        "/asistente/documentos-normativa",
        {"q~uso_equipos": "1", "a~uso_equipos": ["Regla uno concreta"]},
    )

    page = session.get("/asistente/documentos-normativa")

    assert 'name="del~uso_equipos" value="1"' in page.text
    assert 'id="f-uso_equipos-4"' in page.text


def test_a_list_row_is_added_and_removed(session, approved):
    url = "/asistente/documentos-normativa"
    session.post(url, {"q~uso_equipos": "1", "a~uso_equipos": ["Regla uno", "Regla dos", ""]})
    assert stored(approved)["uso_equipos"] == ["Regla uno", "Regla dos"]

    session.post(
        url,
        {"q~uso_equipos": "1", "a~uso_equipos": ["Regla uno", "Regla dos"], "del~uso_equipos": "1"},
    )

    assert stored(approved)["uso_equipos"] == ["Regla dos"]


def test_record_rows_are_added_and_removed(session, approved):
    url = "/asistente/documentos-responsables"
    cargo = ["Gerente", "Seguridad"]
    contacto = ["m@x.example", "l@x.example"]

    def post(**extra):
        return session.post(
            url,
            {
                "q~personas": "1",
                "r~personas~nombre": [MANAGER, SECURITY],
                "r~personas~cargo": cargo,
                "r~personas~contacto": contacto,
                **extra,
            },
        )

    assert post().status_code == 303
    assert [p["nombre"] for p in stored(approved)["personas"]] == [MANAGER, SECURITY]
    assert post(**{"del~personas": "2"}).status_code == 303
    assert [p["nombre"] for p in stored(approved)["personas"]] == [MANAGER]


def test_a_partly_filled_record_is_rejected(session, approved):
    form = {"q~personas": "1", "r~personas~nombre": [MANAGER], "r~personas~cargo": [""],
            "r~personas~contacto": [""]}  # fmt: skip

    response = session.post("/asistente/documentos-responsables", form)

    assert response.status_code == 422
    assert "vacío o de relleno" in response.text


def test_not_applicable_needs_a_concrete_reason(session, approved):
    url = "/asistente/documentos-autorizaciones"

    rejected = session.post(
        url, {"q~aut_moviles": "1", "na~aut_moviles": "1", "reason~aut_moviles": "n/a"}
    )
    accepted = session.post(
        url,
        {
            "q~aut_moviles": "1",
            "na~aut_moviles": "1",
            "reason~aut_moviles": "No hay móviles de empresa",
        },
    )

    assert rejected.status_code == 422
    assert accepted.status_code == 303
    assert stored(approved)["aut_moviles"] == {
        "not_applicable": True,
        "reason": "No hay móviles de empresa",
    }
    assert "No hay móviles de empresa" in session.get(url).text


def test_not_applicable_is_only_offered_where_the_model_allows(session, approved):
    page = session.get("/asistente/documentos-autorizaciones").text

    assert 'name="na~aut_moviles"' in page
    assert 'name="na~aut_instalaciones"' not in page


def test_the_absence_of_resources_question_appears_only_when_it_applies(session, approved):
    url = "/asistente/documentos-responsables"
    assert 'name="a~causa_ausencia_recursos"' not in session.get(url).text

    session.post(
        url, {"q~relacion_seguridad_sistema": "1", "a~relacion_seguridad_sistema": "misma persona"}
    )

    page = session.get(url).text
    assert 'name="a~causa_ausencia_recursos"' in page
    assert 'name="r~medidas_compensatorias~medida"' in page
    assert "solo aparece si" in page


def test_the_same_person_in_both_roles_asks_for_the_cause(session, approved):
    url = "/asistente/documentos-responsables"
    session.post(
        url,
        {
            "q~resp_seguridad": "1",
            "a~resp_seguridad": SECURITY,
            "q~resp_sistema": "1",
            "a~resp_sistema": SECURITY,
        },
    )

    assert 'name="a~causa_ausencia_recursos"' in session.get(url).text


def test_declared_people_are_offered_for_the_responsibles(session, approved):
    url = "/asistente/documentos-responsables"
    session.post(url, form_for("documentos-responsables"))

    page = session.get(url).text

    assert f'<option value="{SECURITY}">' in page
    assert 'list="personas-declaradas"' in page


def test_stepper_marks_a_block_done_only_when_its_answers_are_valid(session, approved):
    def state_of(page: str) -> str:
        return re.search(
            r'href="/asistente/documentos-normativa".*?step-state">([^<]+)<', page, re.S
        ).group(1)

    before = session.get("/asistente/documentos-organizacion").text
    session.post("/asistente/documentos-normativa", form_for("documentos-normativa"))
    after = session.get("/asistente/documentos-organizacion").text

    assert state_of(before) == "pendiente"
    assert state_of(after) == "hecho"


def test_generate_says_what_is_missing_and_links_to_the_block(session, approved):
    page = session.get("/asistente/documentos-generar")

    assert 'href="/asistente/documentos-organizacion"' in page.text
    assert "Falta responder" in page.text
    assert "disabled" in page.text
    assert not CLI.search(page.text)


def test_generate_without_answers_is_refused_in_plain_language(session, approved):
    before = snapshot_of(approved)

    response = session.post("/asistente/documentos-generar", {"accion": "generar"})

    assert response.status_code == 422
    assert "Todavía no has respondido" in response.text
    assert not CLI.search(response.text)
    assert snapshot_of(approved) == before


def test_generate_with_partial_answers_refuses_and_writes_nothing(session, approved):
    session.post("/asistente/documentos-normativa", form_for("documentos-normativa"))
    before = snapshot_of(approved)

    response = session.post("/asistente/documentos-generar", {"accion": "generar"})

    assert response.status_code == 422
    assert "Aún no se pueden generar" in response.text
    assert snapshot_of(approved) == before


def test_generate_needs_an_approved_scope(session, example_store):
    example_store.write_text(ANSWERS_KEY, yaml.safe_dump(ANSWERS, allow_unicode=True))

    page = session.get("/asistente/documentos-generar")
    response = session.post("/asistente/documentos-generar", {"accion": "generar"})

    assert "el alcance no está aprobado" in page.text
    assert response.status_code == 422
    assert "alcance no está aprobado" in response.text


def test_the_whole_flow_generates_and_approves_the_six_documents(session, approved, caplog):
    caplog.set_level(logging.INFO, logger="ens_evidence.ui.audit")
    fill_all(session)

    generated = session.post("/asistente/documentos-generar", {"accion": "generar"})

    assert generated.status_code == 303
    assert "step=documentos-generar" in caplog.text
    assert all(s.state is DocumentState.GENERATED for s in all_statuses(approved))
    roles = dict.fromkeys(
        ("procedimientos-seguridad", "proceso-autorizacion", "procedimiento-incidentes"), SECURITY
    )
    for name in document_names():
        response = session.post(
            "/asistente/documentos-aprobar",
            {"accion": "aprobar", "documento": name, "approved_by": roles.get(name, MANAGER)},
        )
        assert response.status_code == 303, (name, re.findall(r"<li>(.*?)</li>", response.text))
    assert all(s.state is DocumentState.APPROVED for s in all_statuses(approved))
    page = session.get("/asistente/documentos-aprobar").text
    assert page.count("Aprobado") >= 6
    assert "Aprobar este documento" not in page


def test_approval_form_prefills_the_identity_and_names_the_declared_signatory(session, approved):
    fill_all(session)
    session.post("/asistente/documentos-generar", {"accion": "generar"})

    page = session.get("/asistente/documentos-aprobar").text

    assert 'name="approved_by" value="ana@example.com"' in page
    assert f"<strong>{MANAGER}</strong>" in page
    assert f"<strong>{SECURITY}</strong>" in page
    assert "Lo aprueba la dirección" in page
    assert "Lo aprueba el responsable de la seguridad" in page


def test_a_name_that_is_not_the_declared_one_is_rejected_in_plain_words(session, approved):
    fill_all(session)
    session.post("/asistente/documentos-generar", {"accion": "generar"})
    before = snapshot_of(approved)

    response = session.post(
        "/asistente/documentos-aprobar",
        {"accion": "aprobar", "documento": "politica-seguridad", "approved_by": "ana@example.com"},
    )

    assert response.status_code == 422
    assert "no es la persona declarada" in response.text
    assert "firmante_direccion" not in response.text
    assert snapshot_of(approved) == before


def test_approving_without_a_name_marks_that_documents_field(session, approved):
    fill_all(session)
    session.post("/asistente/documentos-generar", {"accion": "generar"})

    response = session.post(
        "/asistente/documentos-aprobar",
        {"accion": "aprobar", "documento": "politica-seguridad", "approved_by": ""},
    )

    assert response.status_code == 422
    assert 'id="err-approved_by~politica-seguridad"' in response.text


def test_approving_an_ungenerated_document_is_refused_without_cli_orders(session, approved):
    fill_all(session)

    response = session.post(
        "/asistente/documentos-aprobar",
        {"accion": "aprobar", "documento": "politica-seguridad", "approved_by": MANAGER},
    )

    assert response.status_code == 422
    assert "no está generado" in response.text
    assert not CLI.search(response.text)


def test_an_unknown_document_is_refused(session, approved):
    response = session.post(
        "/asistente/documentos-aprobar",
        {"accion": "aprobar", "documento": "../x", "approved_by": MANAGER},
    )

    assert response.status_code == 422
    assert "No reconocemos" in response.text


def test_a_generated_document_can_be_read_escaped(session, approved):
    answers = {**ANSWERS, "organizacion": "<b>Aurora</b> S.L., asesoría fiscal y laboral"}
    approved.write_text(ANSWERS_KEY, yaml.safe_dump(answers, allow_unicode=True))
    assert session.post("/asistente/documentos-generar", {"accion": "generar"}).status_code == 303

    page = session.get("/asistente/documentos-aprobar?ver=politica-seguridad")

    assert "<pre>" in page.text
    assert "Aurora" in page.text
    assert "<b>Aurora" not in page.text


def test_reading_an_unknown_document_shows_nothing(session, approved):
    page = session.get("/asistente/documentos-aprobar?ver=../../scope/system")

    assert page.status_code == 200
    assert "<pre>" not in page.text


@pytest.mark.parametrize("slug", [s.slug for s in DOC_STEPS])
def test_pages_have_no_inline_code_nor_forbidden_copy(session, approved, slug):
    fill_all(session)
    session.post("/asistente/documentos-generar", {"accion": "generar"})

    for page in (session.get(f"/asistente/{slug}"), session.post(f"/asistente/{slug}", {"x": "1"})):
        assert page.status_code in (200, 303, 422)
        assert not INLINE.search(page.text)
        assert not FORBIDDEN.search(page.text)


def test_htmx_post_returns_the_fragment_with_the_saved_notice(session, approved):
    response = session.post(
        "/asistente/documentos-normativa", form_for("documentos-normativa"), htmx=True
    )

    assert response.status_code == 200
    assert "<html" not in response.text
    assert "Guardado" in response.text
