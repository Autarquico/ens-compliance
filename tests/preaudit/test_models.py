from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from ens_evidence.models.preaudit import (
    Classification,
    Consent,
    DeterministicRule,
    Disposition,
    DispositionState,
    Finding,
    GroupingProposal,
    Layer,
    LayerStatus,
    PreAuditRun,
    Provider,
    Ref,
    RefKind,
    finding_id,
)

SHA = "a" * 64
AT = datetime(2026, 11, 4, 10, 0, tzinfo=UTC)
DOC = Ref(kind=RefKind.DOCUMENT, id="politica-seguridad")
EVI = Ref(kind=RefKind.EVIDENCE, id="ev-1")
FIXTURES = Path(__file__).parent.parent / "fixtures" / "ccn"


def finding(**over) -> Finding:
    fields = {
        "layer": Layer.JUDGMENT,
        "proposal": Classification.NC_MENOR,
        "measure": "op.acc.1",
        "refs": [DOC, EVI],
        "criterion": "policy-evidence-coherence",
        "reason": "La política exige MFA y la evidencia muestra lo contrario.",
        "quote": "se exige doble factor",
        "source": "IC-01/19 §53",
        "classification_source": "ITS §VI.2",
    } | over
    fields.setdefault("id", finding_id(fields["criterion"], fields["measure"], fields["refs"]))
    return Finding(**fields)


def deterministic(rule: DeterministicRule, proposal: Classification) -> Finding:
    return finding(
        layer=Layer.DETERMINISTIC,
        criterion=rule.value,
        proposal=proposal,
        quote=None,
        refs=[EVI],
        source="ITS §VI.2",
    )


def run(**over) -> PreAuditRun:
    fields = {
        "dossier": "expediente-1",
        "dossier_sha256": SHA,
        "pack_version": "v1",
        "pack_sha256": SHA,
        "at": AT,
    } | over
    return PreAuditRun(**fields)


CONSENT = Consent(by="Ana", at=AT, payload_sha256=SHA, destination="https://api.anthropic.com")
JUDGED = {
    "provider": Provider.ANTHROPIC,
    "model": "m",
    "consent": CONSENT,
    "judgment_status": LayerStatus.DONE,
}


def test_finding_id_is_stable_and_ignores_ref_order():
    assert finding_id("c", "op.acc.1", [DOC, EVI]) == finding_id("c", "op.acc.1", [EVI, DOC])
    assert finding_id("c", "op.acc.1", [DOC]) != finding_id("c", "op.acc.2", [DOC])
    assert finding_id("c", "op.acc.1", [DOC]) != finding_id("d", "op.acc.1", [DOC])


def test_finding_rejects_id_not_derived_from_content():
    with pytest.raises(ValidationError, match="id must derive"):
        finding(id=SHA)


def test_judgment_finding_requires_quote_refs_a_proposal_and_its_classification_source():
    for over, match in [
        ({"quote": None}, "quote"),
        ({"refs": []}, "reference"),
        ({"classification_source": None}, "source of its classification"),
        ({"proposal": Classification.SIN_CLASIFICAR}, "must propose"),
    ]:
        with pytest.raises(ValidationError, match=match):
            finding(**over)


@pytest.mark.parametrize(
    ("rule", "proposal"),
    [
        (DeterministicRule.CHECK_FAIL, Classification.NC_MENOR),
        (DeterministicRule.EVIDENCE_MISSING, Classification.SIN_CLASIFICAR),
        (DeterministicRule.EVIDENCE_EXPIRED, Classification.SIN_CLASIFICAR),
        (DeterministicRule.CHECK_UNKNOWN, Classification.SIN_CLASIFICAR),
        (DeterministicRule.HASH_MISMATCH, Classification.SIN_CLASIFICAR),
        (DeterministicRule.DOSSIER_INTEGRITY, Classification.SIN_CLASIFICAR),
    ],
)
def test_deterministic_finding_follows_the_decision_table(rule, proposal):
    assert deterministic(rule, proposal).proposal is proposal


@pytest.mark.parametrize("rule", list(DeterministicRule))
@pytest.mark.parametrize("wrong", [Classification.NC_MAYOR, Classification.OBSERVACION])
def test_deterministic_finding_never_proposes_major_or_observation(rule, wrong):
    with pytest.raises(ValidationError, match="must be proposed"):
        deterministic(rule, wrong)


def test_deterministic_check_unknown_is_not_a_minor():
    with pytest.raises(ValidationError, match="must be proposed"):
        deterministic(DeterministicRule.CHECK_UNKNOWN, Classification.NC_MENOR)


def test_deterministic_finding_rejects_unknown_rule():
    with pytest.raises(ValidationError, match="unknown deterministic rule"):
        finding(layer=Layer.DETERMINISTIC, criterion="invented")


def test_finding_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        finding(severity="high")


def disposition(state, **over) -> Disposition:
    return Disposition(finding_id=SHA, covers_run=1, state=state, by="Ana", at=AT, **over)


def test_disposition_valid_combinations():
    disposition(DispositionState.OPEN)
    disposition(DispositionState.ACCEPTED, reason="reconocida")
    disposition(DispositionState.RESOLVED, evidence_id="ev-2")
    disposition(DispositionState.DISMISSED, reason="falso positivo")


@pytest.mark.parametrize(
    ("state", "over", "match"),
    [
        (DispositionState.RESOLVED, {}, "RESOLVED requires evidence_id"),
        (DispositionState.DISMISSED, {}, "DISMISSED requires a reason"),
        (DispositionState.DISMISSED, {"reason": ""}, "DISMISSED requires a reason"),
        (DispositionState.OPEN, {"evidence_id": "ev"}, "only valid in RESOLVED"),
        (DispositionState.ACCEPTED, {"evidence_id": "ev"}, "only valid in RESOLVED"),
        (DispositionState.DISMISSED, {"reason": "x", "evidence_id": "ev"}, "only valid"),
    ],
)
def test_disposition_rejects_invalid_combinations(state, over, match):
    with pytest.raises(ValidationError, match=match):
        disposition(state, **over)


def test_disposition_requires_person_and_aware_instant():
    with pytest.raises(ValidationError):
        Disposition(finding_id=SHA, covers_run=1, state=DispositionState.OPEN, by="", at=AT)
    with pytest.raises(ValidationError):
        Disposition(
            finding_id=SHA,
            covers_run=1,
            state=DispositionState.OPEN,
            by="Ana",
            at=datetime(2026, 1, 1),
        )


def grouping(proposal, measures, ids=("a", "b")) -> GroupingProposal:
    return GroupingProposal(
        proposal=proposal,
        measures=measures,
        finding_ids=list(ids),
        justification="agrupación propuesta",
        source="IC-01/19 §54",
    )


def test_grouping_follows_section_54():
    grouping(Classification.NC_MENOR, ["op.acc.1"])
    grouping(Classification.NC_MAYOR, ["op.acc.1", "op.acc.2"])


@pytest.mark.parametrize(
    ("proposal", "measures", "match"),
    [
        (Classification.NC_MENOR, ["op.acc.1", "op.acc.2"], "single measure"),
        (Classification.NC_MAYOR, ["op.acc.1"], "several measures"),
        (Classification.NC_MAYOR, ["op.acc.1", "op.pl.1"], "different groups"),
        (Classification.OBSERVACION, ["op.acc.1"], "NC_MENOR or NC_MAYOR"),
        (Classification.NC_MAYOR, ["op.acc.1", "op.acc.1"], "unique"),
    ],
)
def test_grouping_rejects_what_section_54_forbids(proposal, measures, match):
    with pytest.raises(ValidationError, match=match):
        grouping(proposal, measures)


def test_grouping_needs_at_least_two_distinct_findings():
    with pytest.raises(ValidationError):
        grouping(Classification.NC_MENOR, ["op.acc.1"], ids=("a",))
    with pytest.raises(ValidationError, match="unique"):
        grouping(Classification.NC_MENOR, ["op.acc.1"], ids=("a", "a"))


def test_run_without_provider_has_deterministic_only():
    only = run()
    assert only.judgment_status is LayerStatus.NOT_RUN
    assert only.provider is None
    assert only.consent is None


def test_run_with_judgment_records_provider_and_consent():
    assert run(**JUDGED, discarded=2).discarded == 2


@pytest.mark.parametrize("missing", ["provider", "model", "consent"])
def test_run_with_judgment_requires_every_engine_field(missing):
    with pytest.raises(ValidationError, match="requires"):
        run(**(JUDGED | {missing: None}))


def test_error_run_still_records_who_consented_and_where():
    failed = JUDGED | {"judgment_status": LayerStatus.ERROR, "judgment_error": "fallo"}
    assert run(**failed).consent == CONSENT


def test_judgment_error_goes_only_with_an_error_layer():
    with pytest.raises(ValidationError, match="judgment_error"):
        run(**(JUDGED | {"judgment_status": LayerStatus.ERROR}))
    with pytest.raises(ValidationError, match="judgment_error"):
        run(**(JUDGED | {"judgment_error": "fallo"}))


def test_run_not_run_rejects_provider_consent_and_discards():
    for over in [{"provider": Provider.OPENAI}, {"consent": CONSENT}, {"discarded": 1}]:
        with pytest.raises(ValidationError, match="NOT_RUN"):
            run(**over)


def test_run_rejects_non_completed_deterministic_layer_and_bad_values():
    with pytest.raises(ValidationError, match="always completes"):
        run(deterministic_status=LayerStatus.ERROR)
    with pytest.raises(ValidationError):
        run(dossier_sha256="xyz")
    with pytest.raises(ValidationError):
        run(**JUDGED | {"discarded": -1})


def test_run_rejects_unknown_provider():
    with pytest.raises(ValidationError):
        run(**JUDGED | {"provider": "local"})


@pytest.mark.parametrize(
    ("name", "quotes"),
    [
        (
            "its-auditoria-vi.txt",
            ["VI.2 Los hallazgos de no conformidad", "VI.3 El dictamen final"],
        ),
        (
            "ic0119-52-54.txt",
            ["52. Es imperativo calificar", "54. Para facilitar", "distintos grupos de"],
        ),
    ],
)
def test_ccn_fixtures_carry_provenance_and_the_cited_paragraphs(name, quotes):
    text = (FIXTURES / name).read_text(encoding="utf-8")
    header = text.split("\n\n", 1)[0]
    assert "Fuente: https://" in header
    assert "Descargado: 2026-11-04" in header
    assert "sha256 del documento completo" in header
    assert all(quote in text for quote in quotes)
