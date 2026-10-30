import os
import re
from datetime import date

import pytest

from ens_evidence.models.enums import Role
from ens_evidence.orgdocs import TemplateError, build_documents
from ens_evidence.orgdocs.render import escape_markdown
from ens_evidence.orgdocs.template import Template, check_template
from ens_evidence.scope import approve_scope
from ens_evidence.workspace import LocalStore, StorageUnavailable

from .orgdocs_helpers import (
    APPROVED_AT,
    DOCUMENTS,
    EXAMPLE_SCOPE,
    SCOPE_FILES,
    fixture_answers,
    snapshot,
    write_answers,
)

QUESTION = {"id": "nombre", "text": "¿Cómo se llama?", "clause": "org.1.1"}
LISTA = {"id": "reglas", "text": "¿Qué reglas hay?", "type": "list", "clause": "org.2.1"}
REGISTROS = {
    "id": "tareas",
    "text": "¿Qué tareas hay?",
    "type": "records",
    "clause": "org.3.1",
    "fields": [{"id": "tarea", "text": "Tarea"}, {"id": "quien", "text": "Quién"}],
}


def template(questions, body) -> Template:
    return Template.model_validate(
        {
            "id": "prueba",
            "title": "Prueba",
            "questions": questions,
            "sections": [{"id": "uno", "title": "Uno", "body": body}],
        }
    )


def test_una_variable_desconocida_es_un_error_de_plantilla():
    with pytest.raises(TemplateError, match="variable desconocida otra"):
        check_template(template([QUESTION], "{{nombre}} {{otra}}"), "prueba")


def test_una_pregunta_sin_usar_es_un_error_de_plantilla():
    with pytest.raises(TemplateError, match="pregunta nombre: no se usa en ninguna sección"):
        check_template(template([QUESTION], "texto fijo"), "prueba")


def test_un_campo_de_registro_sin_usar_es_un_error_de_plantilla():
    body = "{{#each tareas}}\n- {{.tarea}}\n{{/each}}"

    with pytest.raises(TemplateError, match=r"campos sin usar \['quien'\]"):
        check_template(template([REGISTROS], body), "prueba")


def test_una_lista_usada_como_texto_es_un_error_de_plantilla():
    with pytest.raises(TemplateError, match="usa #each"):
        check_template(template([LISTA], "{{reglas}}"), "prueba")


def test_un_campo_desconocido_dentro_de_un_bloque_repetido_es_un_error_de_plantilla():
    body = "{{#each tareas}}\n- {{.tarea}} {{.quien}} {{.otro}}\n{{/each}}"

    with pytest.raises(TemplateError, match="no existe en tareas"):
        check_template(template([REGISTROS], body), "prueba")


def test_una_marca_mal_formada_es_un_error_de_plantilla():
    with pytest.raises(TemplateError, match="marca de variable mal formada"):
        check_template(template([QUESTION], "{{nombre}} {{"), "prueba")


CONDICIONAL = {
    "id": "causa",
    "text": "¿Por qué?",
    "clause": "org.1.1",
    "required_if": {"question": "modo", "any_of": ["excepcion"]},
}
MODO = {
    "id": "modo",
    "text": "¿Qué modo?",
    "clause": "org.1.1",
    "choices": ["normal", "excepcion"],
}


def conditional_template(when) -> Template:
    return Template.model_validate(
        {
            "id": "prueba",
            "title": "Prueba",
            "questions": [MODO, CONDICIONAL],
            "sections": [
                {"id": "modo", "title": "Modo", "body": "{{modo}}"},
                {"id": "causa", "title": "Causa", "body": "{{causa}}", "when": when},
            ],
        }
    )


def test_una_pregunta_condicional_en_una_seccion_sin_condicion_es_un_error_de_plantilla():
    with pytest.raises(TemplateError, match="sección causa: usa causa.*`when`"):
        check_template(conditional_template(None), "prueba")


def test_una_pregunta_condicional_en_una_seccion_con_otra_condicion_es_un_error_de_plantilla():
    when = {"question": "modo", "any_of": ["normal", "excepcion"]}

    with pytest.raises(TemplateError, match="sección causa: usa causa"):
        check_template(conditional_template(when), "prueba")


def test_una_pregunta_condicional_en_una_seccion_con_la_misma_condicion_se_acepta():
    check_template(conditional_template({"question": "modo", "any_of": ["excepcion"]}), "prueba")


def test_una_plantilla_correcta_se_acepta():
    assert check_template(template([QUESTION], "Nombre: {{nombre}}"), "prueba")


@pytest.mark.parametrize(
    ("raw", "escaped"),
    [
        ("**negrita** y _cursiva_", r"\*\*negrita\*\* y \_cursiva\_"),
        ("[enlace](http://x.example)", r"\[enlace\](http://x.example)"),
        ("<script>alert(1)</script>", r"\<script\>alert(1)\</script\>"),
        ("a | b", r"a \| b"),
        ("# no es un título", r"\# no es un título"),
        ("- no es una viñeta", r"\- no es una viñeta"),
        ("1. no es una lista", r"1\. no es una lista"),
        ("`código`", r"\`código\`"),
        ("a\n\n    código", "a\n\ncódigo"),
        ("a\n   - viñeta", "a\n\\- viñeta"),
        ("Alta & baja", r"Alta \& baja"),
    ],
)
def test_el_markdown_de_una_respuesta_se_escapa(raw, escaped):
    assert escape_markdown(raw) == escaped


def test_un_texto_multilinea_en_un_elemento_de_lista_no_rompe_la_lista(answered):
    answers = fixture_answers()
    answers["resp_informacion_funciones"] = "Decide el acceso.\n\n    Y la custodia."
    write_answers(answered, answers)

    build_documents(answered)

    document = answered.read_text("org/docs/politica-seguridad.md")
    assert "Decide el acceso. Y la custodia." in document
    assert "\n    Y la custodia" not in document


def test_el_markdown_de_las_respuestas_llega_escapado_al_documento(answered):
    answers = fixture_answers()
    answers["mision"] = "Somos **los mejores** <b>ya</b>\n# Título falso"
    answers["marco_legal"] = ["[RGPD](http://x.example)", "Contrato | cliente"]
    write_answers(answered, answers)

    build_documents(answered)

    document = answered.read_text("org/docs/politica-seguridad.md")
    assert r"Somos \*\*los mejores\*\* \<b\>ya\</b\>" in document
    assert "\n\\# Título falso" in document
    assert r"- \[RGPD\](http://x.example)" in document
    assert r"- Contrato \| cliente" in document
    assert "**los mejores**" not in document


def build_in(tmp_path, name) -> LocalStore:
    store = LocalStore(tmp_path / name)
    for scope_file in SCOPE_FILES:
        store.write_text(
            f"scope/{scope_file}.yaml",
            (EXAMPLE_SCOPE / f"{scope_file}.yaml").read_text(encoding="utf-8"),
        )
    approve_scope(store, "Ana Ruiz", Role.SYSTEM_OWNER, APPROVED_AT)
    write_answers(store, fixture_answers())
    build_documents(store)
    return store


def test_las_mismas_respuestas_producen_los_mismos_bytes(tmp_path):
    first = build_in(tmp_path, "uno")
    second = build_in(tmp_path, "dos")

    keys = [f"org/docs/{n}{ext}" for n in DOCUMENTS for ext in (".md", ".meta.yaml")]
    assert [first.read_bytes(k) for k in keys] == [second.read_bytes(k) for k in keys]


def test_la_salida_no_lleva_la_fecha_de_generacion(built):
    today = date.today().isoformat()
    generated = [built.read_text(k) for k in built.list("org/docs/")]

    assert all(today not in text for text in generated)
    assert not any(re.search(r"generated|created|generado_en", t, re.IGNORECASE) for t in generated)


@pytest.fixture
def failing_replace(monkeypatch):
    """Frontera de fs: el rename final de las escrituras cuyo destino acaba en `suffix` falla."""
    real = os.replace
    failing = {"suffix": None}

    def replace(source, target):
        if failing["suffix"] and str(target).endswith(failing["suffix"]):
            raise OSError("disco lleno")
        real(source, target)

    monkeypatch.setattr(os, "replace", replace)
    return failing


def change_mission(store):
    answers = fixture_answers()
    answers["mision"] = "Una misión nueva y distinta."
    write_answers(store, answers)


def test_si_falla_la_escritura_del_documento_el_anterior_queda_intacto_sin_temporales(
    built, failing_replace
):
    change_mission(built)
    before = snapshot(built)
    failing_replace["suffix"] = "politica-seguridad.md"

    with pytest.raises(StorageUnavailable):
        build_documents(built)

    assert (
        built.read_bytes("org/docs/politica-seguridad.md")
        == before["org/docs/politica-seguridad.md"]
    )
    assert not [k for k in built.list("") if ".tmp-" in k]


def test_si_falla_la_escritura_del_meta_la_siguiente_generacion_archiva_el_documento_huerfano(
    built, failing_replace
):
    change_mission(built)
    failing_replace["suffix"] = "politica-seguridad.meta.yaml"
    with pytest.raises(StorageUnavailable):
        build_documents(built)
    assert not [k for k in built.list("") if ".tmp-" in k]
    failing_replace["suffix"] = None

    report = build_documents(built)

    result = next(d for d in report.documents if d.name == "politica-seguridad")
    assert result.edited_by_hand
    assert "Una misión nueva y distinta." in built.read_text("org/docs/politica-seguridad.md")
    assert built.exists("org/docs/politica-seguridad.meta.yaml")
