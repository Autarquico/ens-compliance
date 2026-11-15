import pytest
import yaml

from ens_evidence.applicability import ApplicabilityRejected, propose_applicability
from ens_evidence.applicability.propose import decide
from ens_evidence.applicability.validate import validate_applicability
from ens_evidence.models.enums import Applicability
from ens_evidence.models.readiness import AbsentAssets

from .helpers import ABSENT_REASON, LATER, measures_by_id

REASON = "Decisión de la organización tras revisar el riesgo."
GROUPED = "op.acc.5"
OPTIONAL = "op.ext.3"


def measure_errors(store, measure_id):
    return [e for e in validate_applicability(store, LATER).errors if f"› {measure_id}:" in e]


def test_decide_registra_la_decision_con_el_fundamento_vigente(workspace):
    decide(workspace, LATER, OPTIONAL, Applicability.NOT_APPLICABLE, REASON)

    measure = measures_by_id(workspace)[OPTIONAL]
    assert measure.applicability is Applicability.NOT_APPLICABLE
    assert measure.justification == REASON
    assert measure.proposed is False
    assert measure.selection is not None
    assert measure_errors(workspace, OPTIONAL) == []


def test_decide_crea_el_fichero_de_medidas_si_falta(tmp_path, workspace):
    (tmp_path / "ws" / "controls" / "measures.yaml").unlink()

    decide(workspace, LATER, OPTIONAL, Applicability.NOT_APPLICABLE, REASON)

    assert measures_by_id(workspace)[OPTIONAL].applicability is Applicability.NOT_APPLICABLE


def test_decide_exige_elegir_un_refuerzo_por_grupo(workspace):
    with pytest.raises(ApplicabilityRejected) as raised:
        decide(workspace, LATER, GROUPED, Applicability.APPLICABLE, REASON)

    assert "elige exactamente un refuerzo" in raised.value.errors[0]
    assert measures_by_id(workspace)[GROUPED].applicability is Applicability.UNDETERMINED


def test_decide_rechaza_un_refuerzo_fuera_de_los_grupos(workspace):
    with pytest.raises(ApplicabilityRejected) as raised:
        decide(workspace, LATER, GROUPED, Applicability.APPLICABLE, REASON, ["R2", "R9"])

    assert any("R9" in e for e in raised.value.errors)


def test_decide_acepta_un_refuerzo_de_cada_grupo(workspace):
    decide(workspace, LATER, GROUPED, Applicability.APPLICABLE, REASON, ["R3"])

    assert measures_by_id(workspace)[GROUPED].chosen_reinforcements == ["R3"]
    assert measure_errors(workspace, GROUPED) == []


def test_decide_rechaza_una_justificacion_vacia(workspace):
    with pytest.raises(ApplicabilityRejected):
        decide(workspace, LATER, OPTIONAL, Applicability.NOT_APPLICABLE, "  ")


@pytest.mark.parametrize("reason", ["no aplica", "N/A.", "corto"])
def test_decide_rechaza_una_justificacion_generica_al_excluir(workspace, reason):
    before = workspace.read_text("controls/measures.yaml")

    with pytest.raises(ApplicabilityRejected, match="razón propia"):
        decide(workspace, LATER, OPTIONAL, Applicability.NOT_APPLICABLE, reason)

    assert workspace.read_text("controls/measures.yaml") == before


def test_decide_no_deja_excluir_una_exigida_sin_ausencia_de_activo(workspace):
    with pytest.raises(ApplicabilityRejected) as raised:
        decide(workspace, LATER, "org.1", Applicability.NOT_APPLICABLE, REASON)

    assert "el anexo II la exige" in raised.value.errors[0]


def test_decide_rechaza_una_ausencia_generica(workspace):
    with pytest.raises(ApplicabilityRejected):
        decide(
            workspace,
            LATER,
            "op.ext.1",
            Applicability.NOT_APPLICABLE,
            REASON,
            absent_assets={
                "asset_type": "servicios prestados por terceros",
                "justification": "no aplica",
            },
        )


def test_decide_rechaza_una_ausencia_fuera_de_la_lista_cerrada(workspace):
    with pytest.raises(ApplicabilityRejected) as raised:
        decide(
            workspace,
            LATER,
            "org.1",
            Applicability.NOT_APPLICABLE,
            REASON,
            absent_assets=AbsentAssets(asset_type="provider", justification=ABSENT_REASON),
        )

    assert "no se refiere a un tipo de activo" in raised.value.errors[0]


def test_decide_rechaza_una_ausencia_que_el_inventario_contradice(workspace):
    with pytest.raises(ApplicabilityRejected) as raised:
        decide(
            workspace,
            LATER,
            "op.ext.1",
            Applicability.NOT_APPLICABLE,
            REASON,
            absent_assets=AbsentAssets(
                asset_type="servicios prestados por terceros", justification=ABSENT_REASON
            ),
        )

    assert "proveedor" in raised.value.errors[0]


def test_decide_conserva_el_resto_de_decisiones_y_la_valoracion(workspace):
    propose_applicability(workspace, LATER)
    before = measures_by_id(workspace)

    decide(workspace, LATER, OPTIONAL, Applicability.NOT_APPLICABLE, REASON)

    after = measures_by_id(workspace)
    assert {k: v for k, v in after.items() if k != OPTIONAL} == {
        k: v for k, v in before.items() if k != OPTIONAL
    }


def test_decide_sobre_una_propuesta_la_hace_decision_humana(workspace):
    propose_applicability(workspace, LATER)
    assert measures_by_id(workspace)["org.1"].proposed is True

    decide(workspace, LATER, "org.1", Applicability.APPLICABLE, "La adoptamos tal cual.")

    measure = measures_by_id(workspace)["org.1"]
    assert (measure.proposed, measure.justification) == (False, "La adoptamos tal cual.")


def test_decide_rechaza_una_medida_ajena_al_catalogo(tmp_path, workspace):
    with pytest.raises(ApplicabilityRejected):
        decide(workspace, LATER, "xx.9", Applicability.APPLICABLE, REASON)

    assert "xx.9" not in yaml.safe_load(
        (tmp_path / "ws" / "controls" / "measures.yaml").read_text()
    )
