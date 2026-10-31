from datetime import timedelta

import pytest
import yaml

from ens_evidence.applicability import declaration_status
from ens_evidence.evidence.layout import manifest_key
from ens_evidence.matrix import (
    CheckLink,
    DocumentLink,
    ManualEvidenceLink,
    StaticCheckResults,
    StaticFindings,
    add_review,
    prepare_review,
)
from ens_evidence.matrix import evaluate as evaluate_module
from ens_evidence.matrix.status import row_status
from ens_evidence.models import (
    Applicability,
    BlockerKind,
    CheckStatus,
    MeasureAssessment,
    Readiness,
    ReadinessInput,
    evaluate_readiness,
)
from ens_evidence.models.readiness import OpenFinding
from ens_evidence.workspace import LocalStore

from .evaluate_helpers import (
    ADDITIONAL,
    FAR,
    NOT_SELECTED,
    NOW,
    ONE_CLAUSE,
    RECORDED,
    TWO_CLAUSES,
    FakeDocuments,
    approved_document,
    assess_now,
    configure,
    details,
    entry,
    re_decide,
    record,
    review_record,
    row_of,
    row_spec,
)
from .helpers import OTHER_SHA, applicability_review
from .source_helpers import CHECK, check_result, manual_evidence, run

MISSING_REVIEW = BlockerKind.MISSING_REVIEW.value
EXPIRED_REVIEW = BlockerKind.EXPIRED_REVIEW.value
MISSING_EVIDENCE = BlockerKind.MISSING_EVIDENCE.value


def evidence_link(store, covers=("base",), **kwargs):
    kwargs.setdefault("valid_until", FAR)
    evidence_id = manual_evidence(store, **kwargs)
    return evidence_id, ManualEvidenceLink(evidence_id=evidence_id, covers=list(covers))


def reviewed_measure(store, measure_id=ONE_CLAUSE, **kwargs):
    """Medida con evidencia vigente, responsable, periodicidad y revisión COMPLIANT."""
    evidence_id, link = evidence_link(store)
    matrix_entry = entry(measure_id, link)
    configure(store, matrix_entry)
    record(store, matrix_entry, evidence_ids=[evidence_id], **kwargs)
    return matrix_entry, evidence_id


# --- escenario 1: evidencia vigente sin revisión ---------------------------------------------


def test_evidencia_vigente_sin_revision_es_un_bloqueo(signed):
    _, link = evidence_link(signed)
    configure(signed, entry(ONE_CLAUSE, link))

    row = row_of(assess_now(signed), ONE_CLAUSE)

    assert (MISSING_REVIEW, "review_missing") in details(row)
    assert not any(kind == MISSING_EVIDENCE for kind, _ in details(row))
    assert row.measure.assessment is MeasureAssessment.NOT_ASSESSED


def test_la_revision_vigente_con_evidencia_deja_la_fila_sin_bloqueos(signed):
    reviewed_measure(signed)

    row = row_of(assess_now(signed), ONE_CLAUSE)

    assert row.blockers == ()
    assert row.measure.assessment is MeasureAssessment.COMPLIANT
    assert row.covered == {"op.exp.1.1"}


def test_revision_caducada_por_valid_until(signed):
    reviewed_measure(signed, valid_days=2)

    row = row_of(assess_now(signed, at=NOW + timedelta(days=3)), ONE_CLAUSE)

    assert (EXPIRED_REVIEW, "") in details(row)


def test_la_vigencia_efectiva_es_la_menor_de_valid_until_y_la_periodicidad(signed):
    reviewed_measure(signed, valid_days=90)

    before = row_of(assess_now(signed, at=NOW + timedelta(days=28)), ONE_CLAUSE)
    after = row_of(assess_now(signed, at=NOW + timedelta(days=31)), ONE_CLAUSE)

    assert before.blockers == ()
    assert (EXPIRED_REVIEW, "") in details(after)
    assert after.measure.review.valid_until == RECORDED - timedelta(hours=1) + timedelta(days=30)


def test_valid_until_corto_manda_sobre_una_periodicidad_larga(signed):
    evidence_id, link = evidence_link(signed)
    matrix_entry = entry(ONE_CLAUSE, link, every=365)
    configure(signed, matrix_entry)
    record(signed, matrix_entry, evidence_ids=[evidence_id], valid_days=5)

    row = row_of(assess_now(signed, at=NOW + timedelta(days=6)), ONE_CLAUSE)

    assert (EXPIRED_REVIEW, "") in details(row)


def test_context_changed_si_la_declaracion_cambia_lo_que_dice_de_la_medida(signed):
    matrix_entry, evidence_id = reviewed_measure(signed)
    other = review_record(
        signed, matrix_entry, evidence_ids=[evidence_id], measure_context_sha256=OTHER_SHA
    )
    declaration_row = row_of(assess_now(signed), ONE_CLAUSE)
    assert declaration_row.blockers == ()

    # Una revisión posterior hecha sobre otro contexto sustituye a la anterior.
    add_review(signed, other.model_copy(update={"recorded_at": NOW - timedelta(hours=6)}))

    row = row_of(assess_now(signed), ONE_CLAUSE)

    assert (EXPIRED_REVIEW, "context_changed") in details(row)


def test_links_changed_si_se_enlaza_otra_fuente_despues_de_revisar(signed):
    matrix_entry, _ = reviewed_measure(signed)
    _, extra = evidence_link(signed, data=b"otra")
    configure(signed, matrix_entry.model_copy(update={"links": [*matrix_entry.links, extra]}))

    row = row_of(assess_now(signed), ONE_CLAUSE)

    assert (EXPIRED_REVIEW, "links_changed") in details(row)


def test_links_changed_si_cambia_covers(signed):
    matrix_entry, _ = reviewed_measure(signed)
    link = matrix_entry.links[0].model_copy(update={"covers": ["op.exp.1.1"]})
    configure(signed, matrix_entry.model_copy(update={"links": [link]}))

    row = row_of(assess_now(signed), ONE_CLAUSE)

    assert (EXPIRED_REVIEW, "links_changed") in details(row)


def test_links_changed_si_se_desvincula_la_fuente(signed):
    matrix_entry, _ = reviewed_measure(signed)
    configure(signed, matrix_entry.model_copy(update={"links": []}))

    row = row_of(assess_now(signed), ONE_CLAUSE)

    assert (EXPIRED_REVIEW, "links_changed") in details(row)
    assert (MISSING_EVIDENCE, "clause:op.exp.1.1") in details(row)


def test_evidence_changed_si_el_artefacto_ya_no_es_el_que_se_reviso(signed):
    evidence_id, link = evidence_link(signed)
    matrix_entry = entry(ONE_CLAUSE, link)
    configure(signed, matrix_entry)
    reviewed = review_record(signed, matrix_entry, evidence_ids=[evidence_id])
    first = reviewed.obligations[0]
    tampered = first.evidence[0].model_copy(update={"artifact_sha256": OTHER_SHA})
    obligation = first.model_copy(update={"evidence": [tampered]})
    add_review(signed, reviewed.model_copy(update={"obligations": [obligation]}))

    row = row_of(assess_now(signed), ONE_CLAUSE)

    assert (EXPIRED_REVIEW, f"evidence_changed:{evidence_id}") in details(row)


def test_document_changed_si_el_documento_aprobado_ya_no_es_el_revisado(signed):
    link = DocumentLink(document="politica", covers=["base"])
    matrix_entry = entry(ONE_CLAUSE, link)
    configure(signed, matrix_entry)
    docs = FakeDocuments(approved_document(measure_id=ONE_CLAUSE, clauses=("op.exp.1.1",)))
    record(signed, matrix_entry, documents=["politica"])
    assert row_of(assess_now(signed, documents=docs), ONE_CLAUSE).blockers == ()

    changed = FakeDocuments(
        approved_document(measure_id=ONE_CLAUSE, clauses=("op.exp.1.1",), sha=OTHER_SHA)
    )
    row = row_of(assess_now(signed, documents=changed), ONE_CLAUSE)

    assert (EXPIRED_REVIEW, "document_changed:politica") in details(row)


def test_una_revision_posterior_a_la_consulta_no_existe(signed):
    evidence_id, link = evidence_link(signed)
    matrix_entry = entry(ONE_CLAUSE, link)
    configure(signed, matrix_entry)
    record(signed, matrix_entry, evidence_ids=[evidence_id], recorded_at=NOW + timedelta(days=1))

    row = row_of(assess_now(signed), ONE_CLAUSE)

    assert (MISSING_REVIEW, "review_missing") in details(row)
    assert row.review is None


def test_sin_responsable_o_sin_periodicidad_es_missing_review(signed):
    evidence_id, link = evidence_link(signed)
    matrix_entry = entry(ONE_CLAUSE, link, every=None, owner=False)
    configure(signed, matrix_entry)
    record(signed, matrix_entry, evidence_ids=[evidence_id])

    row = row_of(assess_now(signed), ONE_CLAUSE)

    assert {(MISSING_REVIEW, "owner_missing"), (MISSING_REVIEW, "review_every_days_missing")} <= (
        details(row)
    )


def test_una_medida_sin_entrada_en_la_matriz_esta_bloqueada(signed):
    row = row_of(assess_now(signed), ONE_CLAUSE)

    assert {(MISSING_REVIEW, "owner_missing"), (MISSING_REVIEW, "review_missing")} <= details(row)
    assert (MISSING_EVIDENCE, "clause:op.exp.1.1") in details(row)


# --- fuentes posteriores a la revisión -------------------------------------------------------


def test_una_evidencia_importada_despues_de_la_revision_no_la_sustenta(signed):
    evidence_id, link = evidence_link(signed, now=RECORDED + timedelta(hours=1))
    matrix_entry = entry(ONE_CLAUSE, link)
    configure(signed, matrix_entry)
    record(signed, matrix_entry, evidence_ids=[evidence_id])

    row = row_of(assess_now(signed), ONE_CLAUSE)

    assert (MISSING_EVIDENCE, f"source_after_review:evidence:{evidence_id}") in details(row)
    assert (MISSING_EVIDENCE, "clause:op.exp.1.1") in details(row)


def test_una_ejecucion_nueva_de_un_check_aceptado_conserva_la_revision(signed):
    link = CheckLink(check_id=CHECK, covers=["base"])
    matrix_entry = entry(ONE_CLAUSE, link)
    configure(signed, matrix_entry)
    record(signed, matrix_entry, checks=[CHECK])
    later = NOW - timedelta(hours=1)
    passing = run([check_result(signed, evaluated_at=later)], finished_at=later)

    row = row_of(assess_now(signed, checks=StaticCheckResults([passing])), ONE_CLAUSE)

    assert row.blockers == ()


# --- test de contrato con la regla anterior --------------------------------------------------


def test_una_medida_que_la_regla_antigua_da_por_buena_la_bloquea_un_hueco_de_la_matriz(signed):
    evidence_id, link = evidence_link(signed, covers=("op.ext.4.1",))
    matrix_entry = entry(TWO_CLAUSES, link)
    configure(signed, matrix_entry)
    record(signed, matrix_entry, evidence_ids=[evidence_id])

    result = assess_now(signed)
    row = row_of(result, TWO_CLAUSES)

    old_rule = evaluate_readiness(
        ReadinessInput(measures=[row.measure], judgment_executed=True, evaluated_at=NOW)
    )
    assert old_rule.readiness is Readiness.READY
    assert (MISSING_EVIDENCE, "clause:op.ext.4.2") in details(row)
    assert result.readiness.readiness is Readiness.NOT_READY
    assert any(b.measure_id == TWO_CLAUSES for b in result.readiness.blockers)
    assert any(b.detail == "clause:op.ext.4.2" for b in result.blockers)


def test_el_resultado_de_preparacion_siempre_pide_juicio_humano(signed):
    assert assess_now(signed).readiness.pending_human_review is True


# --- cobertura por cláusula -------------------------------------------------------------------


def test_cobertura_parcial_deja_la_clausula_sin_cubrir_como_bloqueo(signed):
    evidence_id, link = evidence_link(signed, covers=("op.ext.4.1",))
    matrix_entry = entry(TWO_CLAUSES, link)
    configure(signed, matrix_entry)
    record(signed, matrix_entry, evidence_ids=[evidence_id])

    row = row_of(assess_now(signed), TWO_CLAUSES)

    assert row.covered == {"op.ext.4.1"}
    assert (MISSING_EVIDENCE, "clause:op.ext.4.2") in details(row)
    assert (MISSING_EVIDENCE, "clause:op.ext.4.1") not in details(row)


def test_la_union_de_un_documento_y_una_evidencia_manual_cubre_la_medida(signed):
    evidence_id, manual = evidence_link(signed, covers=("op.ext.4.1",))
    document = DocumentLink(document="politica", covers=["op.ext.4.2"])
    matrix_entry = entry(TWO_CLAUSES, manual, document)
    configure(signed, matrix_entry)
    record(signed, matrix_entry, evidence_ids=[evidence_id], documents=["politica"])
    docs = FakeDocuments(approved_document())

    result = assess_now(signed, documents=docs)
    row = row_of(result, TWO_CLAUSES)

    assert row.covered == {"op.ext.4.1", "op.ext.4.2"}
    assert row.blockers == ()
    assert {e.id for e in row.measure.evidence} == {evidence_id, "document:politica"}


def test_un_documento_obsoleto_da_expired_evidence_y_uno_generado_missing_evidence(signed):
    document = DocumentLink(document="politica", covers=["op.ext.4.2"])
    configure(signed, entry(TWO_CLAUSES, document))

    obsolete = row_of(
        assess_now(signed, documents=FakeDocuments(approved_document(state="OBSOLETE"))),
        TWO_CLAUSES,
    )
    generated = row_of(
        assess_now(signed, documents=FakeDocuments(approved_document(state="GENERATED"))),
        TWO_CLAUSES,
    )

    assert (
        BlockerKind.EXPIRED_EVIDENCE.value,
        "document_not_approved:politica:OBSOLETE",
    ) in details(obsolete)
    assert (MISSING_EVIDENCE, "document_not_approved:politica:GENERATED") in details(generated)
    assert not any(k == BlockerKind.EXPIRED_EVIDENCE.value for k, _ in details(generated))


# --- un FAIL bloquea -------------------------------------------------------------------------


def test_un_fail_bloquea_aunque_haya_una_revision_compliant(signed):
    evidence_id, manual = evidence_link(signed)
    matrix_entry = entry(ONE_CLAUSE, manual, CheckLink(check_id=CHECK, covers=["base"]))
    configure(signed, matrix_entry)
    record(signed, matrix_entry, evidence_ids=[evidence_id], checks=[CHECK])
    failing = run([check_result(signed, status=CheckStatus.FAIL, evidence_ids=[])])

    row = row_of(assess_now(signed, checks=StaticCheckResults([failing])), ONE_CLAUSE)

    assert row.measure.assessment is MeasureAssessment.COMPLIANT
    assert (BlockerKind.NOT_COMPLIANT.value, f"check_failed:{CHECK}:repo-a") in details(row)
    assert row.covered == {"op.exp.1.1"}


# --- Declaración no vigente -------------------------------------------------------------------


def test_declaracion_obsoleta_bloquea_todo_con_un_bloqueo_global(signed):
    reviewed_measure(signed)
    key = "controls/measures.yaml"
    signed.write_text(key, signed.read_text(key) + "\n")

    result = assess_now(signed)

    assert result.declaration_state.value == "STALE"
    assert [(b.kind, b.measure_id, b.detail) for b in result.blockers] == [
        (BlockerKind.UNDETERMINED_APPLICABILITY, None, "declaration_stale")
    ]
    assert result.readiness.readiness is Readiness.NOT_READY
    assert result.rows
    assert all(
        (BlockerKind.UNDETERMINED_APPLICABILITY.value, "declaration_stale") in details(r)
        for r in result.rows
    )


def test_sin_declaracion_firmada_no_hay_filas_y_todo_esta_bloqueado(ready):
    result = assess_now(ready)

    assert result.declaration_state.value == "NOT_APPROVED"
    assert result.rows == ()
    assert result.readiness.readiness is Readiness.NOT_READY
    assert result.blockers[0].detail == "declaration_not_approved"


# --- NOT_APPLICABLE ---------------------------------------------------------------------------


def test_no_aplicable_sin_revision_esta_bloqueada(signed):
    configure(signed, entry(NOT_SELECTED))

    row = row_of(assess_now(signed), NOT_SELECTED)

    assert row.measure.applicability is Applicability.NOT_APPLICABLE
    assert (MISSING_REVIEW, "") in details(row)


def test_no_aplicable_con_revision_vigente_no_da_compliant_ni_pide_evidencia(signed):
    matrix_entry = entry(NOT_SELECTED)
    configure(signed, matrix_entry)
    add_review(
        signed,
        applicability_review(NOT_SELECTED, RECORDED).model_copy(
            update={"measure_context_sha256": _context(signed, NOT_SELECTED)}
        ),
    )

    row = row_of(assess_now(signed), NOT_SELECTED)

    assert row.blockers == ()
    assert row.measure.assessment is MeasureAssessment.NOT_ASSESSED
    assert row.covered == frozenset()


def _context(store, measure_id):
    return row_spec(store, measure_id).measure_context_sha256


def test_una_medida_aplicable_no_acepta_una_revision_de_aplicabilidad(signed):
    evidence_id, link = evidence_link(signed)
    configure(signed, entry(ONE_CLAUSE, link))
    add_review(signed, applicability_review(ONE_CLAUSE, RECORDED))

    row = row_of(assess_now(signed), ONE_CLAUSE)

    assert (EXPIRED_REVIEW, "context_changed") in details(row)
    assert row.measure.assessment is MeasureAssessment.NOT_ASSESSED


def test_de_aplicable_a_no_aplicable_la_revision_anterior_queda_context_changed(signed):
    evidence_id, link = evidence_link(signed)
    matrix_entry = entry(ADDITIONAL, link, every=30)
    configure(signed, matrix_entry)
    record(signed, matrix_entry, evidence_ids=[evidence_id])
    assert row_of(assess_now(signed), ADDITIONAL).blockers == ()

    re_decide(signed, ADDITIONAL, Applicability.NOT_APPLICABLE, NOW)
    row = row_of(assess_now(signed, at=NOW + timedelta(hours=1)), ADDITIONAL)

    assert row.spec.kind.value == "not_selected"
    assert row.measure.applicability is Applicability.NOT_APPLICABLE
    assert (EXPIRED_REVIEW, "context_changed") in details(row)


def test_de_no_aplicable_a_aplicable_la_revision_anterior_queda_context_changed(signed):
    configure(signed, entry(NOT_SELECTED))
    add_review(
        signed,
        applicability_review(NOT_SELECTED, RECORDED).model_copy(
            update={"measure_context_sha256": _context(signed, NOT_SELECTED)}
        ),
    )
    assert row_of(assess_now(signed), NOT_SELECTED).blockers == ()

    re_decide(signed, NOT_SELECTED, Applicability.APPLICABLE, NOW)
    row = row_of(assess_now(signed, at=NOW + timedelta(hours=1)), NOT_SELECTED)

    assert row.spec.kind.value == "selected"
    assert (EXPIRED_REVIEW, "context_changed") in details(row)


def test_la_declaracion_y_las_decisiones_no_cambian_al_evaluar(signed):
    reviewed_measure(signed)
    before = {
        key: signed.read_text(key)
        for key in ("controls/measures.yaml", "controls/applicability.yaml")
    }

    assess_now(signed)

    assert before == {key: signed.read_text(key) for key in before}


# --- ronda 1: una sola revisión efectiva, una sola regla de cobertura --------------------------


def test_ida_y_vuelta_aplicable_no_aplicable_aplicable_no_revive_la_revision_antigua(signed):
    re_decide(signed, ADDITIONAL, Applicability.APPLICABLE, NOW - timedelta(hours=4))
    evidence_id, link = evidence_link(signed)
    matrix_entry = entry(ADDITIONAL, link, every=30)
    configure(signed, matrix_entry)
    reviewed = record(signed, matrix_entry, evidence_ids=[evidence_id])
    assert row_of(assess_now(signed), ADDITIONAL).blockers == ()

    re_decide(signed, ADDITIONAL, Applicability.NOT_APPLICABLE, NOW - timedelta(hours=3))
    add_review(
        signed,
        applicability_review(ADDITIONAL, NOW - timedelta(hours=2)).model_copy(
            update={"measure_context_sha256": _context(signed, ADDITIONAL)}
        ),
    )
    re_decide(signed, ADDITIONAL, Applicability.APPLICABLE, NOW - timedelta(hours=1))
    assert _context(signed, ADDITIONAL) == reviewed.measure_context_sha256

    row = row_of(assess_now(signed), ADDITIONAL)

    assert row.spec.kind.value == "selected"
    assert (EXPIRED_REVIEW, "context_changed") in details(row)
    assert row.measure.assessment is MeasureAssessment.NOT_ASSESSED


def test_la_revision_de_implantacion_no_rellena_la_assessment_de_una_fila_no_aplicable(signed):
    evidence_id, link = evidence_link(signed)
    matrix_entry = entry(ADDITIONAL, link, every=30)
    configure(signed, matrix_entry)
    record(signed, matrix_entry, evidence_ids=[evidence_id])
    re_decide(signed, ADDITIONAL, Applicability.NOT_APPLICABLE, NOW - timedelta(hours=1))

    row = row_of(assess_now(signed), ADDITIONAL)

    assert row.measure.assessment is MeasureAssessment.NOT_ASSESSED
    assert (EXPIRED_REVIEW, "context_changed") in details(row)


def test_review_y_assess_cuentan_las_mismas_clausulas_sin_cubrir(signed):
    _, link = evidence_link(signed, covers=("op.ext.4.1",))
    configure(signed, entry(TWO_CLAUSES, link))

    plan = prepare_review(
        signed,
        NOW,
        TWO_CLAUSES,
        by="Ana",
        role="system_administrator",
        conclusion="Revisada",
        assessment="COMPLIANT",
        documents=FakeDocuments(),
    )
    row = row_of(assess_now(signed), TWO_CLAUSES)

    in_assess = {
        d.removeprefix("clause:")
        for kind, d in details(row)
        if kind == MISSING_EVIDENCE and d.startswith("clause:")
    }
    in_review = {
        p.split("sin cubrir: ")[1].split(" ")[0] for p in plan.problems if "sin cubrir" in p
    }
    assert in_assess == in_review == {"op.ext.4.2"}


def test_la_cobertura_son_las_unidades_que_cubren_las_fuentes_que_cuentan(signed):
    _, link = evidence_link(signed, covers=("op.ext.4.1",))
    matrix_entry = entry(TWO_CLAUSES, link)
    configure(signed, matrix_entry)
    spec = row_spec(signed, TWO_CLAUSES)

    supports = evaluate_module.link_supports(
        signed, spec, matrix_entry, (), FakeDocuments().snapshot(signed, NOW), NOW
    )
    covered = evaluate_module.covered_units(supports)

    assert covered == frozenset({"op.ext.4.1"})
    assert [(u.obligation, u.unit) for u in evaluate_module.uncovered_units(spec, covered)] == [
        ("base", "op.ext.4.2")
    ]


def test_assess_acepta_checks_y_documentos_sin_indicar(signed):
    result = evaluate_module.assess(signed, NOW, checks=None, documents=FakeDocuments())

    assert result.rows


# --- historial ilegible y huellas del manifiesto ----------------------------------------------


def damage(store, name="zzz.yaml", text="axis: implementation\n"):
    key = f"controls/reviews/{ONE_CLAUSE}/{name}"
    store.write_text(key, text)
    return key


def test_un_registro_ilegible_no_reactiva_la_revision_favorable_anterior(signed):
    matrix_entry, evidence_id = reviewed_measure(signed)
    record(
        signed,
        matrix_entry,
        evidence_ids=[evidence_id],
        recorded_at=RECORDED + timedelta(hours=1),
        assessment=MeasureAssessment.NON_COMPLIANT,
    )
    key = damage(signed)

    result = assess_now(signed)
    row = row_of(result, ONE_CLAUSE)

    assert (MISSING_REVIEW, f"review_unreadable:{key}") in details(row)
    assert any(key in error for error in result.errors)
    assert row.measure.review is None and not row_status(row).implemented


@pytest.mark.parametrize("name,text", [("zzz.yaml", "axis: implementation\n"), ("x.yml", "{}\n")])
def test_cualquier_fichero_ilegible_del_historial_bloquea_la_medida_aunque_haya_una_revision_valida(
    signed, name, text
):
    reviewed_measure(signed)
    assert row_of(assess_now(signed), ONE_CLAUSE).blockers == ()
    key = damage(signed, name, text)

    row = row_of(assess_now(signed), ONE_CLAUSE)

    assert (MISSING_REVIEW, f"review_unreadable:{key}") in details(row)
    assert row.review is not None  # se sigue mostrando para consulta
    assert not row_status(row).implemented


def test_el_historial_ilegible_de_una_medida_no_bloquea_a_las_demas(signed):
    reviewed_measure(signed)
    damage(signed)

    other = row_of(assess_now(signed), TWO_CLAUSES)

    assert not any(d.startswith("review_unreadable") for _, d in details(other))


def evidence_manifest_edit(store, evidence_id, edit):
    key = manifest_key(evidence_id)
    data = yaml.safe_load(store.read_text(key))
    edit(data)
    store.write_text(key, yaml.safe_dump(data, sort_keys=False))


@pytest.mark.parametrize(
    "edit",
    [
        lambda d: d.update(valid_until=(NOW + timedelta(days=700)).isoformat()),
        lambda d: d.update(collected_at=(NOW - timedelta(days=2)).isoformat()),
        lambda d: d["provenance"].update(submitted_by="Otra persona"),
    ],
    ids=["valid_until", "collected_at", "procedencia"],
)
def test_evidence_changed_si_cambia_la_vigencia_o_la_procedencia_del_manifiesto(signed, edit):
    _, evidence_id = reviewed_measure(signed)
    assert row_of(assess_now(signed), ONE_CLAUSE).blockers == ()

    evidence_manifest_edit(signed, evidence_id, edit)
    row = row_of(assess_now(signed), ONE_CLAUSE)

    assert (EXPIRED_REVIEW, f"evidence_changed:{evidence_id}") in details(row)


def test_extender_la_vigencia_de_una_evidencia_caducada_no_reactiva_la_revision(signed):
    evidence_id, link = evidence_link(signed, valid_until=NOW + timedelta(days=10))
    matrix_entry = entry(ONE_CLAUSE, link)
    configure(signed, matrix_entry)
    record(signed, matrix_entry, evidence_ids=[evidence_id])
    later = NOW + timedelta(days=20)
    assert row_of(assess_now(signed, later), ONE_CLAUSE).blockers

    extended = (later + timedelta(days=90)).isoformat()
    evidence_manifest_edit(signed, evidence_id, lambda d: d.update(valid_until=extended))
    row = row_of(assess_now(signed, later), ONE_CLAUSE)

    assert (EXPIRED_REVIEW, f"evidence_changed:{evidence_id}") in details(row)
    assert not row_status(row).implemented


# --- ronda 2: volver a un contexto anterior, aprobaciones documentales y claves repetidas ------


def test_ida_y_vuelta_sin_revision_ni_evaluacion_entre_medias_no_revive_la_revision(signed):
    evidence_id, link = evidence_link(signed)
    matrix_entry = entry(ADDITIONAL, link, every=30)
    configure(signed, matrix_entry)
    reviewed = record(signed, matrix_entry, evidence_ids=[evidence_id])
    assert row_of(assess_now(signed), ADDITIONAL).blockers == ()

    # A -> B -> A, dos firmas de la Declaración y ni una evaluación ni una revisión entre medias.
    re_decide(signed, ADDITIONAL, Applicability.NOT_APPLICABLE, NOW - timedelta(hours=3))
    re_decide(signed, ADDITIONAL, Applicability.APPLICABLE, NOW - timedelta(hours=1))
    assert _context(signed, ADDITIONAL) == reviewed.measure_context_sha256

    row = row_of(assess_now(signed), ADDITIONAL)

    assert row.spec.kind.value == "selected"
    assert (EXPIRED_REVIEW, "context_changed") in details(row)
    assert not row_status(row).implemented


def test_volver_a_firmar_con_el_mismo_contexto_no_caduca_la_revision(signed):
    reviewed_measure(signed)
    re_decide(signed, ADDITIONAL, Applicability.APPLICABLE, NOW - timedelta(hours=1))

    assert row_of(assess_now(signed), ONE_CLAUSE).blockers == ()


def test_el_cambio_de_contexto_de_una_medida_no_caduca_la_revision_de_otra(signed):
    reviewed_measure(signed)
    re_decide(signed, ADDITIONAL, Applicability.NOT_APPLICABLE, NOW - timedelta(hours=3))
    re_decide(signed, ADDITIONAL, Applicability.APPLICABLE, NOW - timedelta(hours=1))

    assert row_of(assess_now(signed), ONE_CLAUSE).blockers == ()


def test_la_declaracion_vigente_cuenta_aunque_se_firmara_antes_del_archivo(signed, tmp_path):
    current = declaration_status(signed, NOW).declaration

    signings = evaluate_module.read_signings(LocalStore(tmp_path / "vacio"), current)

    assert signings.declarations == (current,)


def test_una_copia_ilegible_de_una_firma_bloquea_las_medidas_revisadas(signed):
    reviewed_measure(signed)
    key = "controls/declarations/20260101T000000000000Z-zzzzzzzzzzzz.yaml"
    signed.write_text(key, "approved_at: [")

    result = assess_now(signed)
    row = row_of(result, ONE_CLAUSE)

    assert (MISSING_REVIEW, f"declaration_history_unreadable:{key}") in details(row)
    assert any(key in error for error in result.errors)
    assert not row_status(row).implemented


def test_dos_claves_assessment_en_una_revision_posterior_bloquean_la_medida(signed):
    matrix_entry, evidence_id = reviewed_measure(signed)
    later = record(
        signed,
        matrix_entry,
        evidence_ids=[evidence_id],
        recorded_at=RECORDED + timedelta(hours=1),
        assessment=MeasureAssessment.NON_COMPLIANT,
    )
    key = sorted(signed.list(f"controls/reviews/{ONE_CLAUSE}"))[-1]
    assert later.assessment is MeasureAssessment.NON_COMPLIANT
    signed.write_text(key, signed.read_text(key) + "assessment: COMPLIANT\n")

    result = assess_now(signed)
    row = row_of(result, ONE_CLAUSE)

    assert (MISSING_REVIEW, f"review_unreadable:{key}") in details(row)
    assert any(key in error for error in result.errors)
    assert not row_status(row).implemented


def test_una_clave_repetida_anidada_en_una_revision_tambien_bloquea(signed):
    reviewed_measure(signed)
    key = sorted(signed.list(f"controls/reviews/{ONE_CLAUSE}"))[-1]
    text = signed.read_text(key).replace("  reviewer:", "  reviewer: Otra\n  reviewer:", 1)
    signed.write_text(key, text)

    row = row_of(assess_now(signed), ONE_CLAUSE)

    assert (MISSING_REVIEW, f"review_unreadable:{key}") in details(row)
    assert not row_status(row).implemented


# --- checks: problemas de conector y último intento en la agregación compartida ---------------

PROBLEMS = {"github": ("check_history_unreadable:evidence/checks/github/000002.json",)}


def check_measure(store, measure_id=ONE_CLAUSE):
    matrix_entry = entry(measure_id, CheckLink(check_id=CHECK, covers=["base"]))
    configure(store, matrix_entry)
    return matrix_entry


def passing(store):
    later = NOW - timedelta(hours=1)
    return run([check_result(store, evaluated_at=later)], finished_at=later)


def review(store, checks):
    return prepare_review(
        store,
        NOW,
        ONE_CLAUSE,
        by="Ana",
        role="system_administrator",
        conclusion="Revisada",
        assessment="COMPLIANT",
        documents=FakeDocuments(),
        checks=checks,
    )


def test_sin_problemas_de_conector_el_pass_sustenta_y_se_puede_registrar_compliant(signed):
    check_measure(signed)

    plan = review(signed, StaticCheckResults([passing(signed)]))

    assert plan.problems == ()
    assert plan.record is not None


def test_assess_con_un_problema_de_conector_no_sustenta_la_medida_y_lo_informa(signed):
    matrix_entry = check_measure(signed)
    record(signed, matrix_entry, checks=[CHECK])
    source = StaticCheckResults([passing(signed)], problems=PROBLEMS)

    result = assess_now(signed, checks=source)
    row = row_of(result, ONE_CLAUSE)

    assert (MISSING_EVIDENCE, PROBLEMS["github"][0]) in details(row)
    assert row.covered == frozenset()
    assert any(PROBLEMS["github"][0] in error for error in result.errors)
    assert result.readiness.readiness is Readiness.NOT_READY


def test_prepare_review_con_un_problema_de_conector_impide_registrar_compliant(signed):
    check_measure(signed)

    plan = review(signed, StaticCheckResults([passing(signed)], problems=PROBLEMS))

    assert plan.record is None
    assert plan.problems
    assert any(PROBLEMS["github"][0] in note for note in plan.notes)


def test_assess_tras_un_intento_fallido_no_vuelve_al_pass_anterior(signed):
    matrix_entry = check_measure(signed)
    record(signed, matrix_entry, checks=[CHECK])
    failed = run([], run_id="fallido", finished_at=NOW - timedelta(minutes=5), complete=False)

    row = row_of(
        assess_now(signed, checks=StaticCheckResults([passing(signed), failed])), ONE_CLAUSE
    )

    assert (MISSING_EVIDENCE, "check_last_run_failed:github") in details(row)
    assert row.covered == frozenset()


def test_prepare_review_tras_un_intento_fallido_no_registra_compliant(signed):
    check_measure(signed)
    failed = run([], run_id="fallido", finished_at=NOW - timedelta(minutes=5), complete=False)

    plan = review(signed, StaticCheckResults([passing(signed), failed]))

    assert plan.record is None


# --- hallazgos abiertos -----------------------------------------------------------------------


def test_sin_fuente_de_hallazgos_el_juicio_sigue_pendiente_y_no_hay_bloqueo_de_hallazgo(signed):
    result = assess_now(signed)

    assert result.readiness.pending_human_review is True
    assert not any(b.kind is BlockerKind.OPEN_FINDING for b in result.readiness.blockers)


def test_medidas_satisfechas_con_un_hallazgo_abierto_dan_not_ready(signed):
    reviewed_measure(signed)
    finding = OpenFinding(id="F-1", description="Acceso sin MFA")

    result = assess_now(signed, findings=StaticFindings([finding]))

    assert row_of(result, ONE_CLAUSE).blockers == ()
    assert result.readiness.readiness is Readiness.NOT_READY
    assert (BlockerKind.OPEN_FINDING, "F-1") in {
        (b.kind, b.detail) for b in result.readiness.blockers
    }


def test_la_fuente_de_hallazgos_decide_si_el_juicio_se_ejecuto(signed):
    executed = assess_now(signed, findings=StaticFindings([], executed=True))
    pending = assess_now(signed, findings=StaticFindings([], executed=False))

    assert executed.readiness.pending_human_review is False
    assert pending.readiness.pending_human_review is True
