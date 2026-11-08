import pytest
import yaml

from ens_evidence.orgdocs.answers import read_answers, set_answer


def test_set_answer_crea_el_fichero_con_el_esqueleto(scoped):
    set_answer(scoped, "organizacion", "Acme S.L.")

    answers = read_answers(scoped)
    assert answers["organizacion"] == "Acme S.L."
    assert answers["direccion"] == ""
    assert answers["personas"] == []


def test_set_answer_conserva_el_resto(answered):
    before = read_answers(answered)

    set_answer(answered, "organizacion", "Otra S.A.")

    after = read_answers(answered)
    assert after.pop("organizacion") == "Otra S.A."
    before.pop("organizacion")
    assert after == before


def test_set_answer_acepta_lista_de_textos_y_de_registros(scoped):
    set_answer(scoped, "uso_equipos", ["Solo equipos de la empresa", "Bloqueo a los 5 minutos"])
    person = {"nombre": "Ana Ruiz", "cargo": "Gerente", "contacto": "ana@acme.example"}
    set_answer(scoped, "personas", [person])

    answers = read_answers(scoped)
    assert answers["uso_equipos"] == ["Solo equipos de la empresa", "Bloqueo a los 5 minutos"]
    assert answers["personas"] == [person]


def test_set_answer_rechaza_una_clave_desconocida(scoped):
    with pytest.raises(ValueError, match="pregunta desconocida"):
        set_answer(scoped, "no_existe", "x")

    assert not scoped.exists("org/answers.yaml")


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("organizacion", ["lista"]),
        ("uso_equipos", "texto suelto"),
        ("uso_equipos", [1, 2]),
        ("personas", ["Ana"]),
        ("personas", [{"nombre": "Ana", "otro": "x"}]),
        ("relacion_seguridad_sistema", "ninguna de las opciones"),
        ("organizacion", "pendiente"),
        ("poc_servicios", {"not_applicable": True, "reason": "tbd"}),
        ("organizacion", {"not_applicable": True, "reason": "no existe el motivo concreto"}),
    ],
)
def test_set_answer_rechaza_la_forma_incorrecta_sin_escribir(scoped, key, value):
    with pytest.raises(ValueError):
        set_answer(scoped, key, value)

    assert not scoped.exists("org/answers.yaml")


def test_set_answer_admite_no_aplica_con_motivo_donde_se_permite(scoped):
    set_answer(scoped, "info_otras", {"not_applicable": True, "reason": "No hay otra información."})

    raw = yaml.safe_load(scoped.read_text("org/answers.yaml"))
    assert raw["info_otras"] == {"not_applicable": True, "reason": "No hay otra información."}


def test_set_answer_vacio_deja_la_pregunta_sin_responder(answered):
    set_answer(answered, "organizacion", "")

    assert read_answers(answered)["organizacion"] == ""
