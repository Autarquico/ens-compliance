from ens_evidence.models import (
    BlockerKind,
    Measure,
    Readiness,
    ReadinessInput,
    evaluate_readiness,
)
from ens_evidence.models.enums import Applicability, MeasureAssessment
from ens_evidence.models.readiness import Compensation, SelectionBasis

from .helpers import LATER, decide, decide_all_undetermined, measures_by_id


def test_la_aplicabilidad_queda_justificada_por_cli_solo_cuando_toda_medida_esta_decidida(
    workspace, ens_cli
):
    assert ens_cli("applicability", "propose").exit_code == 0

    pending = ens_cli("applicability", "validate")
    assert pending.exit_code == 1
    assert "sin determinar" in pending.output

    decide_all_undetermined(workspace)
    decided = ens_cli("applicability", "validate")
    assert decided.exit_code == 0, decided.output
    assert "Válida" in decided.output

    decide(workspace, "op.cont.2", Applicability.NOT_APPLICABLE, None)
    unjustified = ens_cli("applicability", "validate")
    assert unjustified.exit_code == 1
    assert "falta la justificación" in unjustified.output


def test_firmar_por_cli_exige_validar_y_deja_la_declaracion_vigente(workspace, ens_cli):
    ens_cli("applicability", "propose")

    refused = ens_cli("applicability", "approve", "--by", "Luis Pardo")
    decide_all_undetermined(workspace)
    signed = ens_cli("applicability", "approve", "--by", "Luis Pardo")
    status = ens_cli("applicability", "status")

    assert refused.exit_code == 1
    assert signed.exit_code == 0, signed.output
    assert status.exit_code == 0
    assert "CURRENT" in status.output


def test_status_por_cli_sale_con_error_si_cambia_measures_yaml_tras_firmar(workspace, ens_cli):
    ens_cli("applicability", "propose")
    decide_all_undetermined(workspace)
    ens_cli("applicability", "approve", "--by", "Luis Pardo")
    decide(workspace, "op.cont.3", Applicability.NOT_APPLICABLE, "Justificación reescrita.")

    status = ens_cli("applicability", "status")

    assert status.exit_code == 1
    assert "STALE" in status.output


def test_nada_del_flujo_de_aplicabilidad_produce_conformidad_ni_preparacion(workspace, ens_cli):
    ens_cli("applicability", "propose")
    decide_all_undetermined(workspace)
    ens_cli("applicability", "approve", "--by", "Luis Pardo")

    measures = list(measures_by_id(workspace).values())
    result = evaluate_readiness(
        ReadinessInput(measures=measures, judgment_executed=True, evaluated_at=LATER)
    )

    assert {m.assessment for m in measures} == {MeasureAssessment.NOT_ASSESSED}
    assert result.readiness is Readiness.NOT_READY
    assert BlockerKind.MISSING_REVIEW in {b.kind for b in result.blockers}


def test_la_regla_de_preparacion_sigue_aplicando_con_compensacion_y_fundamento():
    measure = Measure(
        id="op.pl.1",
        applicability=Applicability.APPLICABLE,
        justification="Compensada.",
        compensation=Compensation(
            replaces=["base"], measures=["Otra medida"], justification="Protege igual."
        ),
        selection=SelectionBasis(category="MEDIA", catalog_snapshot_sha256="a" * 64),
        assessment=MeasureAssessment.COMPLIANT,
    )

    result = evaluate_readiness(
        ReadinessInput(measures=[measure], judgment_executed=True, evaluated_at=LATER)
    )

    assert result.readiness is Readiness.NOT_READY
    kinds = {b.kind for b in result.blockers}
    assert {BlockerKind.MISSING_REVIEW, BlockerKind.MISSING_EVIDENCE} <= kinds
