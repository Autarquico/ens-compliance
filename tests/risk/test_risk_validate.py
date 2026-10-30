import pytest

from ens_evidence.risk import init_risk, validate_risk
from ens_evidence.risk.templates import REGISTER_YAML
from ens_evidence.scope import ScopeNotApproved

from .helpers import (
    METHODOLOGY,
    NOW,
    REGISTER,
    acceptance,
    action,
    adopted_methodology,
    dump,
    edit,
    risk,
    write_risks,
)


def problems(store) -> str:
    analysis, errors = validate_risk(store, NOW)
    assert analysis is None
    return "\n".join(errors)


def test_un_analisis_coherente_es_valido(store):
    analysis, errors = validate_risk(store, NOW)

    assert errors == []
    assert [r.id for r in analysis.register.risks] == ["R-001"]
    assert [a.id for a in analysis.plan.actions] == ["A-001"]


def test_init_crea_las_cuatro_plantillas_sin_ningun_valor_de_riesgo(inventory_store):
    created, kept = init_risk(inventory_store)

    assert sorted(created) == [
        "risk/methodology.md",
        "risk/methodology.yaml",
        "risk/register.yaml",
        "risk/treatment-plan.yaml",
    ]
    assert kept == []
    assert inventory_store.read_text(REGISTER) == REGISTER_YAML
    assert "risks: []" in inventory_store.read_text(REGISTER)
    assert "actions: []" in inventory_store.read_text("risk/treatment-plan.yaml")


def test_init_no_sobrescribe_lo_existente(inventory_store):
    inventory_store.write_text(REGISTER, "risks: []  # mío\n")

    created, kept = init_risk(inventory_store)

    assert REGISTER in kept and REGISTER not in created
    assert inventory_store.read_text(REGISTER) == "risks: []  # mío\n"


def test_la_plantilla_recien_creada_no_pasa_por_metodologia_de_la_organizacion(inventory_store):
    init_risk(inventory_store)

    message = problems(inventory_store)

    assert "methodology.yaml › name" in message
    assert "methodology.yaml › reference" in message


def test_sin_ficheros_valida_con_aviso_de_risk_init(inventory_store):
    assert "risk init" in problems(inventory_store)


def test_una_referencia_a_un_activo_inexistente_es_invalida(store):
    write_risks(store, [risk(assets=["fantasma"])], [action()])

    message = problems(store)

    assert "R-001" in message and "assets[0]" in message and "no existe en el inventario" in message
    assert "fantasma" not in message


def test_una_referencia_a_un_servicio_inexistente_es_invalida(store):
    write_risks(store, [risk(services=["fantasma"])], [action()])

    assert "services[0]" in problems(store)


def test_una_salvaguarda_fuera_del_catalogo_es_invalida(store):
    write_risks(store, [risk(safeguards=["op.inventado.9"])], [action()])

    message = problems(store)

    assert "safeguards[0]" in message and "catálogo" in message


def test_un_valor_fuera_de_escala_es_invalido(store):
    write_risks(store, [risk(likelihood="casi-seguro", impact="enorme")], [action()])

    message = problems(store)

    assert "likelihood: no es un valor de la escala" in message
    assert "impact: no es un valor de la escala" in message


def test_un_residual_fuera_de_risk_levels_es_invalido(store):
    write_risks(store, [risk(residual="crítico")], [action()])

    assert "residual: no es un valor de risk_levels" in problems(store)


def test_un_inherente_que_no_casa_con_la_matriz_es_invalido(store):
    write_risks(store, [risk(inherent="bajo")], [action()])

    assert "inherent: no coincide con la matriz" in problems(store)


def test_el_residual_no_puede_superar_al_inherente(store):
    write_risks(
        store,
        [
            risk(
                likelihood="baja",
                impact="bajo",
                inherent="bajo",
                residual="medio",
                acceptance=acceptance(),
            )
        ],
        [action()],
    )

    assert "residual: no puede superar al riesgo inherente" in problems(store)


def test_residual_por_encima_del_minimo_exige_aceptacion_tambien_al_mitigar(store):
    write_risks(store, [risk(treatment="mitigate", residual="medio")], [action()])

    assert "acceptance: el residual no es el mínimo de la escala" in problems(store)


def test_residual_por_encima_del_minimo_con_aceptacion_es_valido(store):
    write_risks(store, [risk(residual="medio", acceptance=acceptance())], [action()])

    _, errors = validate_risk(store, NOW)

    assert errors == []


def test_aceptar_el_riesgo_exige_registrar_la_aceptacion(store):
    write_risks(store, [risk(treatment="accept", safeguards=[], residual="bajo")], [action()])

    assert "acceptance" in problems(store)


def test_una_aceptacion_con_fecha_futura_es_invalida(store):
    future = "2026-12-01T10:00:00+00:00"
    write_risks(store, [risk(residual="medio", acceptance=acceptance(at=future))], [action()])

    assert "accepted_at: la fecha está en el futuro" in problems(store)


def test_una_aceptacion_sin_zona_horaria_es_invalida(store):
    write_risks(
        store,
        [risk(residual="medio", acceptance=acceptance(at="2026-10-20T10:00:00"))],
        [action()],
    )

    assert "accepted_at" in problems(store)


def test_un_plan_con_un_riesgo_inexistente_es_invalido(store):
    write_risks(store, [risk()], [action(risk="R-999")])

    message = problems(store)

    assert "A-001" in message and "no existe en risk/register.yaml" in message


def test_ids_de_riesgo_repetidos_son_invalidos(store):
    write_risks(store, [risk(), risk()], [action()])

    assert "ids de riesgo repetidos" in problems(store)


def test_ids_de_accion_repetidos_son_invalidos(store):
    write_risks(store, [risk()], [action(), action()])

    assert "ids de acción repetidos" in problems(store)


def test_una_metodologia_sin_adoptar_es_invalida(store):
    edit(store, METHODOLOGY, "adopted: true", "adopted: false")

    assert "adopted: true" in problems(store)


def test_una_matriz_con_valores_fuera_de_risk_levels_es_invalida(store):
    methodology = adopted_methodology()
    methodology["matrix"]["baja"]["bajo"] = "inexistente"
    dump(store, METHODOLOGY, methodology)

    assert "matrix" in problems(store)


def test_sin_alcance_aprobado_no_se_valida(store):
    store.write_text("scope/assets.yaml", "assets: []\n")

    with pytest.raises(ScopeNotApproved):
        validate_risk(store, NOW)


def test_un_registro_sin_riesgos_es_invalido(store):
    write_risks(store, [], [])

    assert "no hay ningún riesgo" in problems(store)


def test_un_secreto_en_el_registro_se_rechaza_sin_eco_del_valor(store):
    token = "ghp_" + "A1b2C3d4E5" * 4
    write_risks(store, [risk(threat=f"clave {token}")], [action()])

    message = problems(store)

    assert "secreto" in message
    assert token not in message


def test_dimensiones_y_valoracion_deben_coincidir(store):
    write_risks(store, [risk(dimensions=["C", "I"], valuation={"C": "ALTO"})], [action()])

    assert "valuation" in problems(store)
