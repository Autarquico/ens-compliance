"""La lógica de presentación compartida entre el asistente y la página de documentos."""

import pytest

from ens_evidence.models.enums import Role
from ens_evidence.orgdocs import DocumentState, document_names, load_templates
from ens_evidence.orgdocs.approval import FIXED_AUTHORITY
from ens_evidence.orgdocs.contract import AUTHORITY_ROLE
from ens_evidence.ui.store import UiStore
from ens_evidence.ui.wizard import docs
from ens_evidence.ui.wizard.docs_shared import (
    BLOCK_OF,
    BLOCKS,
    ROLE_LABELS,
    STATE_LABELS,
    STATE_TONES,
    document_rows,
    issue_groups,
    plain,
    questions,
    signatory,
    titles,
)


class FakeQuestion:
    def __init__(self, qid, text):
        self.id, self.text = qid, text


class FakePending:
    def __init__(self, qid, text):
        self.question = FakeQuestion(qid, text)


class FakeReport:
    def __init__(self, pending=(), problems=()):
        self.pending, self.problems = list(pending), list(problems)


def test_el_asistente_sigue_exponiendo_los_mismos_objetos():
    assert docs.BLOCKS is BLOCKS and docs.BLOCK_OF is BLOCK_OF
    assert docs._questions is questions and docs._titles is titles
    assert docs._plain is plain and docs._issue_groups is issue_groups
    assert docs._document_rows is document_rows and docs._signatory is signatory
    assert docs.ROLE_LABELS is ROLE_LABELS and docs.STATE_LABELS is STATE_LABELS


def test_plain_sustituye_ids_por_el_texto_de_la_pregunta():
    qid = next(i for i in questions() if "_" in i)
    text = questions()[qid].text

    assert plain(f"{qid}: falta algo") == f"«{text}»: falta algo"


def test_plain_quita_la_orden_de_linea_de_comandos_y_los_backticks():
    assert plain("hay cambios, revisa los cambios y ejecuta `docs build`") == "hay cambios"
    assert plain("para eso usa `docs build`") == "para eso usa el paso «Generar los documentos»"
    assert "`" not in plain("un `valor` cualquiera")


def test_issue_groups_agrupa_por_bloque_y_deja_lo_desconocido_en_otros_avisos():
    block = BLOCKS[0]
    qid = block.question_ids[0]
    report = FakeReport(
        pending=[FakePending(qid, "Pregunta pendiente")],
        problems=[f"{qid}: mal", "desconocida: aviso raro"],
    )

    groups = issue_groups(report)

    assert groups[0]["slug"] == block.slug and groups[0]["label"] == block.title
    assert groups[0]["items"][0] == "Falta responder: Pregunta pendiente"
    assert len(groups[0]["items"]) == 2
    assert groups[-1]["slug"] is None
    assert groups[-1]["label"] == "Otros avisos"
    assert groups[-1]["items"] == ["desconocida: aviso raro"]


def test_issue_groups_sin_nada_no_devuelve_grupos():
    assert issue_groups(FakeReport()) == []


def test_document_rows_da_una_fila_por_documento(store):
    rows = document_rows(UiStore(store))

    assert [r["name"] for r in rows] == document_names()
    for row in rows:
        state = DocumentState(row["state"])
        assert row["state_label"] == STATE_LABELS[state]
        assert row["tone"] == STATE_TONES[state]
        assert row["title"] == titles()[row["name"]]
        assert all("`" not in reason for reason in row["reasons"])


def test_signatory_es_vacio_sin_rol_o_si_el_titular_no_es_texto():
    assert signatory(None, {}) == ""
    assert signatory(Role.MANAGEMENT, {}) == ""
    from ens_evidence.orgdocs.approval import ROLE_QUESTION

    assert signatory(Role.MANAGEMENT, {ROLE_QUESTION[Role.MANAGEMENT]: ["x"]}) == ""
    assert signatory(Role.MANAGEMENT, {ROLE_QUESTION[Role.MANAGEMENT]: "Marta Gil"}) == "Marta Gil"


@pytest.mark.parametrize("role", {*FIXED_AUTHORITY.values(), *AUTHORITY_ROLE.values()})
def test_role_labels_cubre_todos_los_roles_que_aprueban(role):
    assert role in ROLE_LABELS


def test_questions_y_titles_cubren_las_plantillas():
    loaded = load_templates()
    assert set(titles()) == {item.id for item in loaded}
    assert questions() is questions()
