from datetime import timedelta

import pytest

from ens_evidence.orgdocs import (
    AnswersInvalid,
    DocumentState,
    all_statuses,
    approve_document,
    approved_coverage,
    build_documents,
    document_status,
    load_templates,
)

from .orgdocs_helpers import (
    APPROVED_AT,
    DOCUMENTS,
    SIGNATORY,
    fixture_answers,
    snapshot,
    write_answers,
)
from .test_document_validity import APPROVER, rewrite_approval
from .test_traceable_documents import read_meta

PROCEDURE = "procedimiento-incidentes"
PREVIOUS = [name for name in DOCUMENTS if name != PROCEDURE]
APPROVAL_DAY = APPROVED_AT + timedelta(days=1)
LATER = APPROVED_AT + timedelta(days=30)
ARTICULADO_BEFORE_INCIDENTS = "e59affab976f6e6d1cf03cea8c7e53bd254bfc734cd16d9270f84f2f3d307b86"
INCIDENT_CLAUSES = {"art.25.2", "art.33.7", "op.exp.7.1", "op.exp.7.2"}


def approve(store, name, at=APPROVAL_DAY):
    role = APPROVER[name]
    return approve_document(store, name, SIGNATORY[role], role, at)


@pytest.fixture
def approved_before_incidents(built):
    """Cinco aprobaciones hechas contra el articulado anterior a esta ampliación."""
    for name in DOCUMENTS:
        approve(built, name)
    for name in PREVIOUS:
        rewrite_approval(built, name, articulado_sha256=ARTICULADO_BEFORE_INCIDENTS)
    return built


def test_las_cinco_aprobaciones_previas_pasan_a_obsoletas_por_el_articulado(
    approved_before_incidents,
):
    for name in PREVIOUS:
        status = document_status(approved_before_incidents, name)

        assert status.state is DocumentState.OBSOLETE, name
        assert "fuente del articulado cambiada" in status.reasons, name

    assert document_status(approved_before_incidents, PROCEDURE).state is DocumentState.APPROVED


def test_las_aprobaciones_obsoletas_pierden_su_cobertura(approved_before_incidents):
    trace = approved_coverage(approved_before_incidents)

    assert set(trace.measures) == {"op.exp.7"}
    assert not {c for c in trace.clauses if c.startswith(("org.", "art.2.", "art.11", "art.13"))}
    assert {"op.exp.7.1", "op.exp.7.2", "art.25.2", "art.33.7"} <= set(trace.clauses)


def test_regenerar_y_aprobar_de_nuevo_recupera_la_cobertura(approved_before_incidents):
    build_documents(approved_before_incidents)
    for name in PREVIOUS:
        approve(approved_before_incidents, name, LATER)

    assert {s.state for s in all_statuses(approved_before_incidents)} == {DocumentState.APPROVED}
    assert {"org.1", "org.2", "org.3", "org.4", "op.exp.7"} <= set(
        approved_coverage(approved_before_incidents).measures
    )


def test_el_procedimiento_cita_el_articulo_25_2_y_el_33_7_desde_el_articulado(built):
    clauses = {c.id: c for c in read_meta(built, PROCEDURE).clauses}

    assert INCIDENT_CLAUSES <= set(clauses)
    for clause_id in ("art.25.2", "art.33.7"):
        assert clauses[clause_id].origin == "articulado"


def test_el_procedimiento_no_da_por_cubierto_el_articulo_25_1(built):
    """Exige ajustarse al art. 33 y a una ITS que la plantilla no recoge: se pregunta, no cubre."""
    assert "art.25.1" not in {c.id for c in read_meta(built, PROCEDURE).clauses}


@pytest.mark.parametrize(
    ("authority", "sentence"),
    [
        ("el responsable de la seguridad", "Lo aprueba: el responsable de la seguridad."),
        ("el responsable del sistema", "Lo aprueba: el responsable del sistema."),
        ("la dirección", "Lo aprueba: la dirección, en cuyo nombre firma Marta Ibáñez Soler."),
    ],
)
def test_el_procedimiento_muestra_la_autoridad_declarada_y_solo_esa(built, authority, sentence):
    answers = fixture_answers()
    answers["autoridad_procedimientos"] = authority
    write_answers(built, answers)
    build_documents(built)

    text = built.read_text(f"org/docs/{PROCEDURE}.md")

    assert text.count("Lo aprueba:") == 1
    assert sentence in text


def test_sin_servicios_a_entidades_publicas_no_se_cubre_el_articulo_33_7(built):
    answers = fixture_answers()
    answers["servicios_externalizados"] = "no"
    for question in ("poc_servicios", "incibe_quien", "incibe_como", "incibe_cuando"):
        del answers[question]
    write_answers(built, answers)

    build_documents(built)

    covered = {c.id for c in read_meta(built, PROCEDURE).clauses}
    assert "art.33.7" not in covered
    assert INCIDENT_CLAUSES - {"art.33.7"} <= covered
    assert (
        "no presta a entidades públicas servicios que se apoyen en sus sistemas"
        in built.read_text(f"org/docs/{PROCEDURE}.md")
    )


@pytest.mark.parametrize("question", ["incibe_quien", "incibe_como", "incibe_cuando"])
def test_prestar_servicios_a_entidades_publicas_exige_el_cauce_de_notificacion(answered, question):
    answers = fixture_answers()
    del answers[question]
    write_answers(answered, answers)
    before = snapshot(answered)

    with pytest.raises(AnswersInvalid, match=question):
        build_documents(answered)

    assert snapshot(answered) == before


@pytest.mark.parametrize(
    "removal",
    [
        "incidentes_alcance",
        "incidentes_deteccion",
        "incidentes_informe_eventos",
        "incidentes_niveles",
        "incidentes_analisis",
        "incidentes_comunicacion",
        "incidentes_registro",
        "incidentes_mejora",
        "incidentes_datos_personales",
        "incidentes_evidencia",
        "incidentes_eventos_auditables",
    ],
)
def test_una_respuesta_incompleta_del_procedimiento_no_genera_ningun_documento(answered, removal):
    answers = fixture_answers()
    del answers[removal]
    write_answers(answered, answers)
    before = snapshot(answered)

    with pytest.raises(AnswersInvalid, match=removal):
        build_documents(answered)

    assert snapshot(answered) == before


@pytest.mark.parametrize("field", ["nivel", "criterio", "escalado"])
def test_un_nivel_de_clasificacion_incompleto_no_genera_nada(answered, field):
    answers = fixture_answers()
    del answers["incidentes_niveles"][0][field]
    write_answers(answered, answers)
    before = snapshot(answered)

    with pytest.raises(AnswersInvalid, match="incidentes_niveles"):
        build_documents(answered)

    assert snapshot(answered) == before


def test_el_procedimiento_muestra_cada_nivel_con_su_criterio_y_escalado(built):
    text = built.read_text(f"org/docs/{PROCEDURE}.md")

    for level in fixture_answers()["incidentes_niveles"]:
        assert level["nivel"] in text
        assert level["criterio"] in text
        assert level["escalado"] in text


def test_ninguna_plantilla_cubre_op_exp_9():
    covered = {
        (loaded.id, clause)
        for loaded in load_templates()
        for section in loaded.template.sections
        for clause in section.covers
        if clause.startswith("op.exp.9")
    }

    assert covered == set()


def test_las_preguntas_de_op_exp_9_se_hacen_sin_que_el_documento_cubra_la_medida(built):
    template = next(t for t in load_templates() if t.id == PROCEDURE).template
    questions = {q.id: q.clause for q in template.questions}

    assert questions["incidentes_evidencia"] == "op.exp.9.2"
    assert questions["incidentes_eventos_auditables"] == "op.exp.9.3"
    meta = read_meta(built, PROCEDURE)
    assert not [c.id for c in meta.clauses if c.id.startswith("op.exp.9")]
    assert not [m.id for m in meta.measures if m.id == "op.exp.9"]


def _question(question_id: str):
    template = next(t for t in load_templates() if t.id == PROCEDURE).template
    return next(q for q in template.questions if q.id == question_id)


def test_la_notificacion_al_incibe_no_invita_a_excluir_niveles():
    help_text = _question("incibe_cuando").help

    assert "si se notifica" not in help_text
    assert "incidentes que os afecten" in help_text
    assert "umbrales y plazos dependen de la Instrucción Técnica de Seguridad" in help_text
    assert "no ha verificado" in help_text


def test_la_respuesta_modelo_notifica_todos_los_niveles_declarados():
    levels = [row["nivel"] for row in fixture_answers()["incidentes_niveles"]]
    answer = fixture_answers()["incibe_cuando"]

    assert all(level in answer for level in levels)
    assert "no se notifican" not in answer


def test_la_rama_afirmativa_y_la_negativa_dicen_solo_lo_que_se_pregunto(built):
    answers = fixture_answers()
    write_answers(built, answers)
    build_documents(built)
    yes = built.read_text(f"org/docs/{PROCEDURE}.md")

    answers["servicios_externalizados"] = "no"
    for question in ("poc_servicios", "incibe_quien", "incibe_como", "incibe_cuando"):
        del answers[question]
    write_answers(built, answers)
    build_documents(built)
    no = built.read_text(f"org/docs/{PROCEDURE}.md")

    assert "presta a entidades públicas servicios que se apoyan en sus sistemas" in yes
    assert "Si presta cualquier servicio a una entidad pública, revisad el art. 33.7" in no
    assert "no presta servicios a entidades públicas" not in no


def test_la_pregunta_del_registro_lleva_al_registro_de_la_herramienta():
    help_text = _question("incidentes_registro").help

    assert "ens-evidence incident" in help_text
    assert "único que la matriz acepta para op.exp.9" in help_text
    assert "ens-evidence incident" in fixture_answers()["incidentes_registro"]
