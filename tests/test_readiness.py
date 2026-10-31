from datetime import timedelta, timezone

import pytest
from conftest import NOW
from pydantic import ValidationError

from ens_evidence.models import (
    Applicability,
    Blocker,
    BlockerKind,
    Measure,
    MeasureAssessment,
    Readiness,
    ReadinessInput,
    Review,
    Role,
    evaluate_readiness,
    measure_blockers,
)
from ens_evidence.models.readiness import EvidenceRef, OpenFinding


def review(valid_until=None, reviewed_at=None):
    return Review(
        reviewer="ana",
        role=Role.SECURITY_OFFICER,
        reviewed_at=reviewed_at or NOW - timedelta(days=10),
        valid_until=valid_until or NOW + timedelta(days=30),
        conclusion="ok",
    )


def good_measure(**overrides):
    base = {
        "id": "m1",
        "applicability": Applicability.APPLICABLE,
        "assessment": MeasureAssessment.COMPLIANT,
        "evidence": [EvidenceRef(id="ev-1", valid_until=NOW + timedelta(days=30))],
        "review": review(),
    }
    return Measure(**{**base, **overrides})


def evaluate(*measures, findings=(), judgment_executed=True):
    return evaluate_readiness(
        ReadinessInput(
            measures=list(measures),
            open_findings=[OpenFinding(id=f) for f in findings],
            judgment_executed=judgment_executed,
            evaluated_at=NOW,
        )
    )


def kinds(result):
    return [b.kind for b in result.blockers]


def test_medida_completa_da_ready_sin_bloqueos():
    result = evaluate(good_measure())
    assert result.readiness is Readiness.READY
    assert result.blockers == []


def test_applicable_sin_evidencia_bloquea_por_missing_evidence():
    result = evaluate(good_measure(evidence=[]))
    assert result.readiness is Readiness.NOT_READY
    assert kinds(result) == [BlockerKind.MISSING_EVIDENCE]


def test_evidencia_caducada_bloquea_incluida_la_que_caduca_justo_ahora():
    for valid_until in (NOW - timedelta(days=1), NOW):
        result = evaluate(good_measure(evidence=[EvidenceRef(id="ev-1", valid_until=valid_until)]))
        assert kinds(result) == [BlockerKind.EXPIRED_EVIDENCE]


def test_evidencia_sin_valid_until_no_caduca():
    assert evaluate(good_measure(evidence=[EvidenceRef(id="ev-1")])).readiness is Readiness.READY


def test_revision_caducada_bloquea_por_expired_review():
    result = evaluate(good_measure(review=review(valid_until=NOW)))
    assert kinds(result) == [BlockerKind.EXPIRED_REVIEW]


def tomorrow_review():
    return review(reviewed_at=NOW + timedelta(days=1), valid_until=NOW + timedelta(days=365))


def test_applicable_con_revision_fechada_en_el_futuro_bloquea_por_review_in_future():
    result = evaluate(good_measure(review=tomorrow_review()))
    assert result.readiness is Readiness.NOT_READY
    assert result.blockers == [
        Blocker(kind=BlockerKind.MISSING_REVIEW, measure_id="m1", detail="review_in_future")
    ]


def test_not_applicable_con_revision_fechada_en_el_futuro_bloquea_por_review_in_future():
    measure = good_measure(
        applicability=Applicability.NOT_APPLICABLE,
        justification="no aplica",
        evidence=[],
        review=tomorrow_review(),
    )
    result = evaluate(measure)
    assert result.readiness is Readiness.NOT_READY
    assert result.blockers == [
        Blocker(kind=BlockerKind.MISSING_REVIEW, measure_id="m1", detail="review_in_future")
    ]


def test_revision_fechada_justo_ahora_cuenta_como_vigente():
    result = evaluate(good_measure(review=review(reviewed_at=NOW)))
    assert result.readiness is Readiness.READY
    assert result.blockers == []


def test_applicable_sin_revision_bloquea_por_missing_review():
    assert kinds(evaluate(good_measure(review=None))) == [BlockerKind.MISSING_REVIEW]


@pytest.mark.parametrize(
    "assessment",
    [
        MeasureAssessment.NON_COMPLIANT,
        MeasureAssessment.REQUIRES_REVIEW,
        MeasureAssessment.NOT_ASSESSED,
    ],
)
def test_applicable_no_compliant_bloquea_por_not_compliant(assessment):
    result = evaluate(good_measure(assessment=assessment))
    assert kinds(result) == [BlockerKind.NOT_COMPLIANT]
    assert result.readiness is Readiness.NOT_READY


@pytest.mark.parametrize("assessment", list(MeasureAssessment))
def test_undetermined_nunca_da_ready_con_cualquier_evaluacion(assessment):
    result = evaluate(good_measure(applicability=Applicability.UNDETERMINED, assessment=assessment))
    assert result.readiness is Readiness.NOT_READY
    assert kinds(result) == [BlockerKind.UNDETERMINED_APPLICABILITY]


@pytest.mark.parametrize("justification", [None, "", "   "])
def test_not_applicable_sin_justificacion_bloquea(justification):
    measure = good_measure(
        applicability=Applicability.NOT_APPLICABLE, justification=justification, evidence=[]
    )
    assert kinds(evaluate(measure)) == [BlockerKind.NOT_APPLICABLE_WITHOUT_JUSTIFICATION]


def test_not_applicable_exige_revision():
    measure = good_measure(
        applicability=Applicability.NOT_APPLICABLE, justification="no aplica", review=None
    )
    assert kinds(evaluate(measure)) == [BlockerKind.MISSING_REVIEW]


def test_not_applicable_justificada_y_revisada_es_ready():
    measure = good_measure(
        applicability=Applicability.NOT_APPLICABLE, justification="no aplica", evidence=[]
    )
    assert evaluate(measure).readiness is Readiness.READY


def test_hallazgo_abierto_bloquea_aunque_las_medidas_esten_bien():
    result = evaluate(good_measure(), findings=["f-1"])
    assert result.readiness is Readiness.NOT_READY
    assert result.blockers == [Blocker(kind=BlockerKind.OPEN_FINDING, detail="f-1")]


def test_una_medida_mala_basta_para_not_ready():
    result = evaluate(good_measure(), good_measure(id="m2", review=None))
    assert result.readiness is Readiness.NOT_READY
    assert [b.measure_id for b in result.blockers] == ["m2"]


@pytest.mark.parametrize("executed", [True, False])
def test_pending_human_review_refleja_judgment_executed_y_no_bloquea(executed):
    result = evaluate(good_measure(), judgment_executed=executed)
    assert result.pending_human_review is (not executed)
    assert result.readiness is Readiness.READY


def test_sin_medidas_no_hay_ready_por_omision():
    with pytest.raises(ValidationError):
        ReadinessInput(measures=[], judgment_executed=True, evaluated_at=NOW)


def evidence_collected_at(collected_at):
    return good_measure(
        evidence=[
            EvidenceRef(id="ev-1", valid_until=NOW + timedelta(days=30), collected_at=collected_at)
        ]
    )


def test_evidencia_posterior_a_la_evaluacion_no_cuenta():
    result = evaluate(evidence_collected_at(NOW + timedelta(seconds=1)))
    assert result.readiness is Readiness.NOT_READY
    assert [(b.kind, b.detail) for b in result.blockers] == [
        (BlockerKind.MISSING_EVIDENCE, "evidence_in_future:ev-1")
    ]


def test_evidencia_recogida_en_el_instante_de_la_evaluacion_cuenta():
    assert evaluate(evidence_collected_at(NOW)).readiness is Readiness.READY


def test_evidencia_sin_collected_at_sigue_siendo_valida():
    assert evaluate(evidence_collected_at(None)).readiness is Readiness.READY


def test_zonas_horarias_se_comparan_por_instante_y_no_por_hora_local():
    plus_two = timezone(timedelta(hours=2))
    minus_five = timezone(timedelta(hours=-5))
    local_now = NOW.astimezone(plus_two)
    # misma hora de reloj que NOW, pero en UTC-5 es 5 horas posterior: futura
    wall_clock_equal = NOW.replace(tzinfo=minus_five)
    assert evaluate(evidence_collected_at(local_now)).readiness is Readiness.READY
    assert evaluate(evidence_collected_at(wall_clock_equal)).readiness is Readiness.NOT_READY


def test_una_evidencia_futura_no_anula_a_otra_valida():
    measure = good_measure(
        evidence=[
            EvidenceRef(id="ev-1", collected_at=NOW + timedelta(days=1)),
            EvidenceRef(id="ev-2", collected_at=NOW - timedelta(days=1)),
        ]
    )
    assert evaluate(measure).readiness is Readiness.READY


def test_evidencia_futura_caducada_no_genera_bloqueo_de_caducidad():
    measure = good_measure(
        evidence=[
            EvidenceRef(id="ev-1", collected_at=NOW + timedelta(days=1)),
            EvidenceRef(id="ev-2", valid_until=NOW - timedelta(days=1)),
        ]
    )
    assert kinds(evaluate(measure)) == [BlockerKind.EXPIRED_EVIDENCE]


def gap(measure_id="m1", detail="clause:x.1", kind=BlockerKind.MISSING_EVIDENCE):
    return Blocker(kind=kind, measure_id=measure_id, detail=detail)


def evaluate_with_gaps(*gaps, measures=None):
    return evaluate_readiness(
        ReadinessInput(
            measures=measures or [good_measure()],
            judgment_executed=True,
            evaluated_at=NOW,
            coverage_gaps=list(gaps),
        )
    )


def test_un_hueco_de_cobertura_bloquea_una_medida_que_la_regla_da_por_buena():
    assert evaluate(good_measure()).readiness is Readiness.READY

    result = evaluate_with_gaps(gap())

    assert result.readiness is Readiness.NOT_READY
    assert result.blockers == [gap()]


def test_un_hueco_no_duplica_un_bloqueo_que_la_regla_ya_da():
    measure = good_measure(review=None)
    rule = Blocker(kind=BlockerKind.MISSING_REVIEW, measure_id="m1")

    result = evaluate_with_gaps(rule, gap(), gap(), measures=[measure])

    assert result.blockers == [rule, gap()]


def test_coverage_gaps_con_una_medida_inexistente_se_rechaza():
    with pytest.raises(ValidationError, match="m9"):
        evaluate_with_gaps(gap("m9"))


def test_coverage_gaps_sin_medida_se_rechaza():
    with pytest.raises(ValidationError):
        evaluate_with_gaps(Blocker(kind=BlockerKind.MISSING_EVIDENCE))


def test_measure_blockers_es_la_regla_publica_de_una_medida():
    assert measure_blockers(good_measure(review=None), NOW) == [
        Blocker(kind=BlockerKind.MISSING_REVIEW, measure_id="m1")
    ]
