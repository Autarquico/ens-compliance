import pytest

from ens_evidence.orgdocs import (
    AnswersInvalid,
    all_questions,
    build_documents,
    load_templates,
    official_clause,
)
from ens_evidence.scope import ScopeNotApproved, revoke_approval

from .orgdocs_helpers import APPROVED_AT, ens, fixture_answers, snapshot, write_answers

QUESTIONS = {q.id: q for q in all_questions(load_templates())}
UNCONDITIONAL = [q.id for q in QUESTIONS.values() if q.required and q.required_if is None]
MISSING_MISSION = (
    "pendiente mision: ¿Cuál es la misión de la organización y qué servicios presta? "
    "(cláusula org.1.1; documentos: politica-seguridad)"
)


def build_failing(store) -> AnswersInvalid:
    with pytest.raises(AnswersInvalid) as raised:
        build_documents(store)
    return raised.value


def with_answers(store, **changes):
    answers = fixture_answers()
    answers.update(changes)
    write_answers(store, answers)


def without_answer(store, question_id):
    answers = fixture_answers()
    del answers[question_id]
    write_answers(store, answers)


@pytest.mark.parametrize("question_id", UNCONDITIONAL)
def test_la_generacion_falla_nombrando_la_pregunta_pendiente_exacta(scoped, question_id):
    without_answer(scoped, question_id)
    question = QUESTIONS[question_id]

    failure = build_failing(scoped)

    assert [p.question.id for p in failure.report.pending] == [question_id]
    (message,) = failure.report.errors()
    assert message.startswith(
        f"pendiente {question_id}: {question.text} (cláusula {question.clause}"
    )


def test_el_mensaje_de_la_pregunta_pendiente_lleva_id_texto_clausula_y_documentos(scoped):
    with_answers(scoped, mision="")

    assert build_failing(scoped).report.errors() == [MISSING_MISSION]


def test_sin_respuestas_pendientes_no_se_escribe_nada_en_el_workspace(scoped):
    with_answers(scoped, mision="")
    before = snapshot(scoped)

    build_failing(scoped)

    assert snapshot(scoped) == before


def test_con_documentos_ya_generados_una_respuesta_pendiente_no_los_toca(built):
    with_answers(built, mision="")
    before = snapshot(built)

    build_failing(built)

    assert snapshot(built) == before


def test_docs_build_por_cli_falla_con_la_pregunta_y_sin_escribir_nada(scoped, settings):
    with_answers(scoped, mision="")
    before = snapshot(scoped)

    result = ens(settings, "docs", "build")

    assert result.exit_code == 1
    assert MISSING_MISSION in result.output
    assert snapshot(scoped) == before


@pytest.mark.parametrize("empty", [None, "", "   ", "\n"])
def test_una_obligatoria_vacia_es_una_pregunta_pendiente(scoped, empty):
    with_answers(scoped, mision=empty)

    assert [p.question.id for p in build_failing(scoped).report.pending] == ["mision"]


@pytest.mark.parametrize(
    "filler",
    ["n/a", "N/A.", "-", "---", "pendiente", " Pendiente ", "por definir", "Por Definir.", "TBD"]
    + ["lorem ipsum", "Lorem ipsum dolor sit amet", "ND"],
)
def test_un_valor_de_relleno_inequivoco_cuenta_como_vacio(scoped, filler):
    with_answers(scoped, mision=filler)

    assert [p.question.id for p in build_failing(scoped).report.pending] == ["mision"]


def test_repetir_la_pregunta_cuenta_como_vacio(scoped):
    with_answers(scoped, mision=QUESTIONS["mision"].text)

    assert [p.question.id for p in build_failing(scoped).report.pending] == ["mision"]


def test_una_lista_vacia_es_una_pregunta_pendiente(scoped):
    with_answers(scoped, objetivos_seguridad=[])

    assert [p.question.id for p in build_failing(scoped).report.pending] == ["objetivos_seguridad"]


def test_un_elemento_de_relleno_en_una_lista_es_un_error_explicito(scoped):
    with_answers(scoped, marco_legal=["RGPD", "n/a"])

    failure = build_failing(scoped)

    assert failure.report.errors() == ["marco_legal: el elemento 2 está vacío o es de relleno"]


def test_un_campo_obligatorio_de_relleno_en_un_registro_es_un_error_explicito(scoped):
    comite = fixture_answers()["comite"]
    comite[0]["ambito"] = "por definir"
    with_answers(scoped, comite=comite)

    (message,) = build_failing(scoped).report.errors()

    assert message.startswith("comite: registro 1, «Ámbito de responsabilidad»")


def test_no_aplica_con_motivo_es_valido_donde_la_pregunta_lo_admite(answered):
    build_documents(answered)

    assert "No aplica: No hay otras actividades" in answered.read_text(
        "org/docs/procedimientos-seguridad.md"
    )


def test_no_aplica_en_una_pregunta_que_no_lo_admite_se_rechaza(scoped):
    with_answers(scoped, mision={"not_applicable": True, "reason": "No procede en esta empresa"})

    failure = build_failing(scoped)

    assert failure.report.errors() == ["mision: esta pregunta no admite «no aplica»"]


@pytest.mark.parametrize("reason", ["", "n/a", "pendiente", "-"])
def test_no_aplica_sin_motivo_concreto_se_rechaza(scoped, reason):
    with_answers(scoped, info_otras={"not_applicable": True, "reason": reason})

    assert build_failing(scoped).report.errors() == [
        "info_otras: «no aplica» exige un motivo concreto"
    ]


def test_las_sospechas_de_relleno_son_avisos_y_no_impiden_generar(answered):
    with_answers(
        answered,
        mision="Gestoría laboral de [nombre del sector] en Zaragoza.",
        resolucion_conflictos=fixture_answers()["coordinacion"],
    )

    report = build_documents(answered)

    assert report.warnings == [
        "mision: parece contener un marcador sin sustituir",
        "resolucion_conflictos: repite literalmente la respuesta de coordinacion",
    ]
    assert answered.exists("org/docs/politica-seguridad.md")


def test_docs_build_por_cli_muestra_los_avisos_sin_fallar(answered, settings):
    with_answers(answered, mision="Gestoría laboral de [nombre del sector] en Zaragoza.")

    result = ens(settings, "docs", "build")

    assert result.exit_code == 0, result.output
    assert "Aviso: mision: parece contener un marcador sin sustituir" in result.output


def test_repetir_el_texto_oficial_es_un_aviso(answered):
    with_answers(answered, mision=official_clause("org.1.1").text)

    report = build_documents(answered)

    assert "mision: repite el texto oficial de la cláusula" in report.warnings


def test_una_opcion_fuera_de_las_admitidas_es_un_error_explicito(scoped):
    with_answers(scoped, relacion_seguridad_sistema="quizá")

    (message,) = build_failing(scoped).report.errors()

    assert message.startswith("relacion_seguridad_sistema: debe ser una de estas opciones")


def test_una_pregunta_desconocida_es_un_error(scoped):
    with_answers(scoped, inventada="x")

    assert build_failing(scoped).report.errors() == [
        "org/answers.yaml: pregunta desconocida inventada"
    ]


@pytest.mark.parametrize("relation", ["misma persona", "dependencia jerárquica"])
def test_art_13_3_sin_causa_ni_medidas_compensatorias_deja_las_dos_pendientes(scoped, relation):
    same_person = {"resp_sistema": fixture_answers()["resp_seguridad"]}
    with_answers(
        scoped,
        relacion_seguridad_sistema=relation,
        **(same_person if relation == "misma persona" else {}),
    )

    failure = build_failing(scoped)

    assert [(p.question.id, p.question.clause) for p in failure.report.pending] == [
        ("causa_ausencia_recursos", "art.13.3"),
        ("medidas_compensatorias", "art.13.3"),
    ]
    assert all("designacion-responsables" in p.documents for p in failure.report.pending)


def test_art_13_3_con_causa_y_medidas_genera_la_seccion_de_excepcion(answered):
    with_answers(
        answered,
        resp_sistema=fixture_answers()["resp_seguridad"],
        relacion_seguridad_sistema="misma persona",
        causa_ausencia_recursos="Solo hay tres personas y una sola con formación técnica.",
        medidas_compensatorias=[
            {
                "medida": "Revisión trimestral de los accesos de administración",
                "verifica": "La gerente",
                "verificacion": "Acta firmada con la lista de cuentas revisadas",
            }
        ],
    )

    build_documents(answered)

    document = answered.read_text("org/docs/designacion-responsables.md")
    assert "Solo hay tres personas y una sola con formación técnica." in document
    assert "Revisión trimestral de los accesos de administración" in document


def test_art_13_3_con_personas_distintas_no_exige_causa_ni_medidas(answered):
    build_documents(answered)

    document = answered.read_text("org/docs/designacion-responsables.md")
    assert "Excepción por falta de recursos" not in document


def test_art_13_5_con_servicios_prestados_exige_el_poc_o_su_causa_justificada(scoped):
    answers = fixture_answers()
    assert answers["servicios_externalizados"] == "sí"
    without_answer(scoped, "poc_servicios")

    failure = build_failing(scoped)

    assert [(p.question.id, p.question.clause) for p in failure.report.pending] == [
        ("poc_servicios", "art.13.5")
    ]


def test_art_13_5_con_poc_declarado_genera_la_seccion(answered):
    with_answers(
        answered,
        servicios_externalizados="sí",
        poc_servicios=[
            {
                "servicio": "Gestión de nóminas",
                "cliente": "Ayuntamiento de Utebo",
                "poc": "Lucía Navarro Gil, lucia@gestoriaaurora.example",
                "relacion": "Es la propia responsable de la seguridad de la gestoría.",
            }
        ],
    )

    build_documents(answered)

    assert "Gestión de nóminas (Ayuntamiento de Utebo)" in answered.read_text(
        "org/docs/politica-seguridad.md"
    )


def test_art_13_5_admite_no_designar_poc_con_causa_justificada(answered):
    with_answers(
        answered,
        servicios_externalizados="sí",
        poc_servicios={
            "not_applicable": True,
            "reason": "El contrato con el cliente designa a su propio responsable como contacto.",
        },
    )

    build_documents(answered)

    politica = answered.read_text("org/docs/politica-seguridad.md")
    assert (
        "No se designa punto de contacto. Causa justificada: El contrato con el cliente" in politica
    )


def test_art_13_5_sin_servicios_prestados_no_exige_poc(answered):
    answers = fixture_answers()
    answers["servicios_externalizados"] = "no"
    del answers["poc_servicios"]
    write_answers(answered, answers)

    assert build_documents(answered).documents


def test_sin_alcance_aprobado_no_se_genera_nada(answered):
    revoke_approval(answered, "prueba", APPROVED_AT)
    before = snapshot(answered)

    with pytest.raises(ScopeNotApproved):
        build_documents(answered)

    assert snapshot(answered) == before


def test_docs_build_sin_alcance_aprobado_habla_en_castellano(answered, settings):
    revoke_approval(answered, "prueba", APPROVED_AT)

    result = ens(settings, "docs", "build")

    assert result.exit_code == 1
    assert "pendiente de aprobación" in result.output
    assert "NOT_APPROVED" not in result.output


def test_las_respuestas_de_si_o_no_se_redactan_como_frases_en_la_politica(answered):
    with_answers(answered, servicios_externalizados="no")
    build_documents(answered)

    politica = answered.read_text("org/docs/politica-seguridad.md")

    assert "La organización no presta servicios a otras entidades" in politica
    assert "¿La organización presta" not in politica
