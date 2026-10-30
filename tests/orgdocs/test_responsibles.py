import pytest

from ens_evidence.models.enums import Role
from ens_evidence.orgdocs import (
    AnswersInvalid,
    DocumentApprovalRejected,
    all_questions,
    approve_document,
    build_documents,
    load_templates,
    requirement_label,
    skeleton,
    validate_answers,
)

from .orgdocs_helpers import (
    APPROVED_AT,
    ens,
    fixture_answers,
    write_answers,
)

TEMPLATES = load_templates()
EXCEPTION = ("causa_ausencia_recursos", "medidas_compensatorias")
SAME = "misma persona"


def validate(**changes):
    answers = fixture_answers()
    answers.update(changes)
    return validate_answers(answers, TEMPLATES)


def test_los_responsables_deben_figurar_en_la_lista_de_personas():
    report = validate(resp_servicio="Departamento de administración")

    assert report.errors() == [
        "resp_servicio: «Departamento de administración» no figura en la lista de personas"
    ]


def test_la_coincidencia_con_la_lista_ignora_tildes_y_mayusculas():
    assert validate(resp_servicio="raul campos vidal").valid


def test_misma_persona_en_seguridad_y_sistema_no_admite_la_relacion_distintas():
    same = fixture_answers()["resp_seguridad"]

    report = validate(resp_sistema=same, relacion_seguridad_sistema="distintas")

    assert report.problems[0].startswith("relacion_seguridad_sistema:")


@pytest.mark.parametrize(
    "relation", ["distintas y sin dependencia jerárquica", "dependencia jerárquica"]
)
def test_la_misma_persona_se_deriva_de_los_nombres_y_contradice_otras_relaciones(relation):
    same = fixture_answers()["resp_seguridad"]

    report = validate(resp_sistema=same, relacion_seguridad_sistema=relation)

    assert any("son la misma persona" in p for p in report.problems)
    assert {p.question.id for p in report.pending} == set(EXCEPTION)


def test_misma_persona_declarada_con_personas_distintas_es_una_contradiccion():
    report = validate(relacion_seguridad_sistema=SAME)

    assert any("personas distintas" in p for p in report.problems)


def test_la_misma_persona_con_su_justificacion_es_valida_y_sin_aviso_de_repeticion():
    same = fixture_answers()["resp_seguridad"]

    report = validate(
        resp_sistema=same,
        relacion_seguridad_sistema=SAME,
        causa_ausencia_recursos="Solo hay tres personas y una sola con formación técnica.",
        medidas_compensatorias=[
            {"medida": "Revisión trimestral", "verifica": "La gerente", "verificacion": "Acta"}
        ],
    )

    assert report.valid
    assert report.warnings == []


def test_la_generacion_falla_si_la_misma_persona_declara_que_son_distintas(answered):
    answers = fixture_answers()
    answers["resp_sistema"] = answers["resp_seguridad"]
    write_answers(answered, answers)

    with pytest.raises(AnswersInvalid):
        build_documents(answered)


def test_las_condicionales_se_describen_con_su_condicion():
    by_id = {q.id: q for q in all_questions(TEMPLATES)}

    assert requirement_label(by_id["mision"]) == "obligatoria"
    assert requirement_label(by_id["causa_ausencia_recursos"]).startswith(
        "obligatoria si relacion_seguridad_sistema es misma persona"
    )


def test_docs_questions_y_el_esqueleto_no_dicen_obligatoria_a_secas_en_las_condicionales(settings):
    result = ens(settings, "docs", "questions")

    (line,) = [x for x in result.output.splitlines() if x.startswith("causa_ausencia_recursos")]
    assert "obligatoria si relacion_seguridad_sistema es" in line
    comments = skeleton(TEMPLATES)
    assert "cláusula art.13.3; obligatoria si relacion_seguridad_sistema es" in comments
    assert "solo obligatoria" not in comments


@pytest.mark.parametrize(
    ("role", "by"),
    [
        (Role.MANAGEMENT, "Pepe"),
        (Role.MANAGEMENT, "Raúl Campos Vidal"),
        (Role.SECURITY_OFFICER, "Hugo Serrano Ortiz"),
    ],
)
def test_quien_aprueba_debe_ser_una_persona_declarada_con_el_cargo_del_rol(built, role, by):
    name = "politica-seguridad" if role is Role.MANAGEMENT else "procedimientos-seguridad"

    with pytest.raises(DocumentApprovalRejected):
        approve_document(built, name, by, role, APPROVED_AT.replace(day=29))

    assert built.list(f"org/docs/approvals/{name}/") == []


def test_la_direccion_puede_ser_un_organo_y_firma_la_persona_declarada(built):
    answers = fixture_answers()
    answers["direccion"] = "El consejo de administración de Gestoría Aurora S.L."
    write_answers(built, answers)
    build_documents(built)

    approval = approve_document(
        built, "politica-seguridad", "Marta Ibáñez Soler", Role.MANAGEMENT, APPROVED_AT
    )

    assert approval.approved_by == "Marta Ibáñez Soler"


@pytest.mark.parametrize("by", ["Marta", "Ibáñez", "Marta Ibáñez Soler y otros"])
def test_el_firmante_se_compara_entero_no_por_fragmentos(built, by):
    with pytest.raises(DocumentApprovalRejected, match="firmante_direccion"):
        approve_document(built, "politica-seguridad", by, Role.MANAGEMENT, APPROVED_AT)


def test_el_nombre_dentro_del_texto_de_la_direccion_no_basta(built):
    answers = fixture_answers()
    answers["personas"].append(
        {"nombre": "Aurora Gil", "cargo": "Administrativa", "contacto": "a@x.example"}
    )
    answers["direccion"] = "La gerente, Marta Ibáñez Soler, de Gestoría Aurora Gil S.L."
    write_answers(built, answers)
    build_documents(built)

    with pytest.raises(DocumentApprovalRejected, match="firmante_direccion"):
        approve_document(built, "politica-seguridad", "Aurora Gil", Role.MANAGEMENT, APPROVED_AT)


def test_quien_firma_por_la_direccion_debe_figurar_en_la_lista_de_personas():
    report = validate(firmante_direccion="Pepe Pérez")

    assert report.errors() == ["firmante_direccion: «Pepe Pérez» no figura en la lista de personas"]


def test_sin_firmante_de_la_direccion_la_politica_queda_pendiente(scoped):
    answers = fixture_answers()
    del answers["firmante_direccion"]
    write_answers(scoped, answers)

    with pytest.raises(AnswersInvalid) as failure:
        build_documents(scoped)

    assert any(e.startswith("pendiente firmante_direccion:") for e in failure.value.report.errors())


def test_la_autoridad_se_declara_en_castellano_y_se_traduce_a_rol(built):
    answers = fixture_answers()
    answers["autoridad_procedimientos"] = "security_officer"

    report = validate_answers(answers, TEMPLATES)

    assert report.problems[0].startswith("autoridad_procedimientos:")
    assert validate(autoridad_procedimientos="el responsable del sistema").valid
    assert {"la dirección", "el responsable de la seguridad"} <= set(
        next(q for q in all_questions(TEMPLATES) if q.id == "autoridad_procedimientos").choices
    )


def test_la_direccion_declarada_aprueba_con_su_nombre_en_cualquier_forma(built):
    approve_document(
        built,
        "politica-seguridad",
        "marta ibanez soler",
        Role.MANAGEMENT,
        APPROVED_AT.replace(day=29),
    )


def test_docs_approve_por_cli_rechaza_a_quien_no_es_la_direccion(built, settings):
    result = ens(
        settings, *("docs", "approve", "politica-seguridad", "--by", "Pepe", "--role", "management")
    )

    assert result.exit_code == 1
    assert "no es la persona declarada en firmante_direccion" in result.output
