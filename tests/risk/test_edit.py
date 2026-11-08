import pytest
import yaml

from ens_evidence.risk import RiskState, init_risk, risk_status, validate_risk
from ens_evidence.risk.approval import approve_risk
from ens_evidence.risk.edit import (
    RiskEditRejected,
    add_action,
    add_risk,
    adopt_methodology,
    remove_action,
    remove_risk,
)

from .helpers import ACCEPTED_AT, METHODOLOGY, NOW, PLAN, REGISTER, action, risk


def loaded(store, key):
    return yaml.safe_load(store.read_text(key))


@pytest.fixture
def fresh(inventory_store):
    """Alcance aprobado, plantillas de risk/ creadas y metodología adoptada."""
    adopt_methodology(inventory_store, "M", "R", "A")
    return inventory_store


def test_adopt_methodology_crea_las_plantillas_si_faltan_y_marca_adoptada(inventory_store):
    created = adopt_methodology(inventory_store, "Metodología X", "Guía Y", "Tres niveles")

    assert set(created) == {
        "risk/methodology.md",
        "risk/methodology.yaml",
        "risk/register.yaml",
        "risk/treatment-plan.yaml",
    }
    methodology = loaded(inventory_store, METHODOLOGY)
    assert methodology["adopted"] is True
    assert (methodology["name"], methodology["reference"]) == ("Metodología X", "Guía Y")
    assert [v["id"] for v in methodology["likelihood"]] == ["baja", "media", "alta"]
    assert inventory_store.read_text(METHODOLOGY).startswith("# PROPUESTA editable")


def test_adopt_methodology_rechaza_campos_vacios_sin_escribir(fresh):
    before = fresh.read_text(METHODOLOGY)

    with pytest.raises(RiskEditRejected):
        adopt_methodology(fresh, "  ", "Guía", "Adaptación")

    assert fresh.read_text(METHODOLOGY) == before


def test_add_risk_conserva_la_cabecera_y_valida(fresh):
    add_risk(fresh, risk(), NOW)

    assert fresh.read_text(REGISTER).startswith("# Un riesgo por elemento")
    assert [r["id"] for r in loaded(fresh, REGISTER)["risks"]] == ["R-001"]


def test_add_risk_rechaza_inherente_que_no_sale_de_la_matriz(fresh):
    with pytest.raises(RiskEditRejected) as caught:
        add_risk(fresh, risk(inherent="bajo"), NOW)

    assert any("inherent" in e for e in caught.value.errors)
    assert loaded(fresh, REGISTER)["risks"] == []


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"assets": ["no-existe"]}, "assets"),
        ({"safeguards": ["xx.yy.9"]}, "safeguards"),
        ({"likelihood": "rarisima", "inherent": "bajo"}, "likelihood"),
    ],
)
def test_add_risk_rechaza_referencias_que_no_existen(fresh, overrides, field):
    with pytest.raises(RiskEditRejected) as caught:
        add_risk(fresh, risk(**overrides), NOW)

    assert any(field in e for e in caught.value.errors)


def test_add_risk_rechaza_id_repetido(fresh):
    add_risk(fresh, risk(), NOW)

    with pytest.raises(RiskEditRejected, match="ya existe"):
        add_risk(fresh, risk(), NOW)


def test_add_risk_exige_aceptacion_si_el_residual_no_es_el_minimo(fresh):
    with pytest.raises(RiskEditRejected) as caught:
        add_risk(fresh, risk(residual="medio", treatment="mitigate"), NOW)

    assert any("acceptance" in e for e in caught.value.errors)
    accepted = {"accepted_by": "Ana Ruiz", "role": "system_owner", "accepted_at": ACCEPTED_AT}
    add_risk(fresh, risk(residual="medio", acceptance=accepted), NOW)
    assert loaded(fresh, REGISTER)["risks"][0]["acceptance"]["accepted_by"] == "Ana Ruiz"


def test_add_risk_rechaza_secretos_sin_nombrar_el_valor(fresh):
    secret = "AKIAIOSFODNN7EXAMPLE"
    with pytest.raises(RiskEditRejected) as caught:
        add_risk(fresh, risk(threat=f"clave {secret}"), NOW)

    assert secret not in " ".join(caught.value.errors)


def test_remove_risk_se_niega_si_una_accion_lo_cita(fresh):
    add_risk(fresh, risk(), NOW)
    add_action(fresh, action())

    with pytest.raises(RiskEditRejected, match="A-001"):
        remove_risk(fresh, "R-001")

    remove_action(fresh, "A-001")
    remove_risk(fresh, "R-001")
    assert loaded(fresh, REGISTER)["risks"] == []


def test_remove_de_id_inexistente_se_rechaza(fresh):
    with pytest.raises(RiskEditRejected):
        remove_risk(fresh, "R-9")
    with pytest.raises(RiskEditRejected):
        remove_action(fresh, "A-9")


def test_add_action_exige_un_riesgo_existente_y_fecha_valida(fresh):
    with pytest.raises(RiskEditRejected, match="R-001"):
        add_action(fresh, action())

    add_risk(fresh, risk(), NOW)
    with pytest.raises(RiskEditRejected):
        add_action(fresh, action(due="mañana"))
    add_action(fresh, action())
    assert [a["id"] for a in loaded(fresh, PLAN)["actions"]] == ["A-001"]


def test_el_registro_editado_valida_y_se_aprueba(inventory_store):
    from ens_evidence.categorization import approve_categorization

    from .helpers import write_category

    write_category(inventory_store, "MEDIO")
    approve_categorization(inventory_store, "Marta Gil", NOW)
    adopt_methodology(inventory_store, "M", "R", "A")
    inventory_store.write_text(
        "risk/methodology.md", "# Metodología\n\nLa que usa la organización.\n"
    )
    add_risk(inventory_store, risk(), NOW)
    add_action(inventory_store, action())

    analysis, errors = validate_risk(inventory_store, NOW)
    assert errors == []
    assert analysis is not None
    approve_risk(inventory_store, "Marta Gil", NOW)
    assert risk_status(inventory_store, NOW).state is RiskState.APPROVED

    remove_action(inventory_store, "A-001")
    assert risk_status(inventory_store, NOW).state is RiskState.STALE


def test_add_risk_pide_adoptar_antes_la_metodologia(inventory_store):
    init_risk(inventory_store)

    with pytest.raises(RiskEditRejected) as caught:
        add_risk(inventory_store, risk(), NOW)

    assert any("risk/methodology.yaml" in e for e in caught.value.errors)
