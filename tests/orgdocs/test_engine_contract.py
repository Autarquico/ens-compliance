import pytest

from ens_evidence.orgdocs import load_templates
from ens_evidence.orgdocs.template import LoadedTemplate, TemplateError, check_engine_contract


def mutated(change) -> list[LoadedTemplate]:
    """Plantillas cargadas con un cambio sobre cada pregunta (copias, sin tocar las reales)."""
    result = []
    for loaded in load_templates():
        questions = [change(q.model_copy(deep=True)) for q in loaded.template.questions]
        template = loaded.template.model_copy(update={"questions": questions})
        result.append(LoadedTemplate(template, loaded.version, loaded.bodies))
    return result


def rename(old, new):
    def change(question):
        if question.id == old:
            question.id = new
        return question

    return change


def test_las_plantillas_reales_cumplen_el_contrato_del_motor():
    check_engine_contract(load_templates())


@pytest.mark.parametrize(
    "question_id",
    ["resp_sistema", "personas", "relacion_seguridad_sistema", "firmante_direccion"]
    + ["autoridad_procedimientos", "incidentes_niveles"],
)
def test_renombrar_una_pregunta_de_la_que_depende_el_motor_rompe_la_carga(question_id):
    with pytest.raises(TemplateError, match=question_id):
        check_engine_contract(mutated(rename(question_id, question_id + "_x")))


def test_quitar_el_campo_nombre_de_las_personas_rompe_la_carga():
    def change(question):
        if question.id == "personas":
            question.fields = [f for f in question.fields if f.id != "nombre"]
        return question

    with pytest.raises(TemplateError, match="nombre"):
        check_engine_contract(mutated(change))


@pytest.mark.parametrize("field", ["nivel", "criterio", "escalado"])
def test_quitar_un_campo_de_los_niveles_de_incidentes_rompe_la_carga(field):
    def change(question):
        if question.id == "incidentes_niveles":
            question.fields = [f for f in question.fields if f.id != field]
        return question

    with pytest.raises(TemplateError, match=field):
        check_engine_contract(mutated(change))


def test_quitar_la_opcion_misma_persona_rompe_la_carga():
    def change(question):
        if question.id == "relacion_seguridad_sistema":
            question.choices = [c for c in question.choices if c != "misma persona"]
        return question

    with pytest.raises(TemplateError, match="misma persona"):
        check_engine_contract(mutated(change))


def test_las_opciones_de_autoridad_son_las_que_el_dominio_sabe_traducir():
    def change(question):
        if question.id == "autoridad_procedimientos":
            question.choices = [*question.choices[:-1], "el comité"]
        return question

    with pytest.raises(TemplateError, match="autoridad_procedimientos"):
        check_engine_contract(mutated(change))
