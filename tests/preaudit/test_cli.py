"""Uso de la CLI de la pre-auditoría. Los escenarios de la spec están en `test_acceptance.py`."""

from datetime import UTC, datetime

import pytest
from assessment.dossier.stages import AFTER, basic

from ens_evidence.cli import REDACTION_LIMIT, discard_breakdown, disposition_line, print_finding
from ens_evidence.models.preaudit import (
    Classification,
    DeterministicRule,
    DiscardReason,
    Disposition,
    DispositionState,
    Finding,
    Layer,
    Ref,
    RefKind,
    finding_id,
)
from ens_evidence.preaudit.classify import judgment_classification_source, proposal_for, source_for
from ens_evidence.preaudit.deterministic import RULE_LABELS
from ens_evidence.preaudit.findings import FindingState
from ens_evidence.preaudit.service import PREAUDITS_PREFIX, SHELL_RISK

from .helpers import configure, fake_codex, world_at

P = ["preaudit"]


@pytest.fixture(scope="module")
def stages():
    return basic()


@pytest.fixture
def world(stages, tmp_path, monkeypatch):
    world = world_at(stages, tmp_path, monkeypatch)
    world.at(AFTER)
    return world


# --- uso ---------------------------------------------------------------------------------------


def test_prepare_without_settings_is_a_configuration_error(world, stages):
    prepared = world.run(*P, "prepare", stages.dossier)

    assert prepared.exit_code == 2
    assert "configura `preaudit`" in prepared.output


def test_a_malformed_consent_is_a_usage_error(world, stages):
    ran = world.run(*P, "run", stages.dossier, "--consent", "abc", "--by", "Ana")

    assert ran.exit_code == 2 and "sha256" in ran.output
    assert world.store.list(PREAUDITS_PREFIX) == []


# --- copy del consentimiento y de los resultados ------------------------------------------------


def consent_part(output: str) -> str:
    """Lo que sigue al payload: lo que se lee justo antes de consentir."""
    return output.split("\nExpediente:", 1)[1]


def test_prepare_warns_of_the_redaction_limit_before_asking_for_consent(world, stages):
    configure(world)

    prepared = world.run(*P, "prepare", stages.dossier)

    assert prepared.exit_code == 0, prepared.output
    shown = consent_part(prepared.output)
    assert shown.index(REDACTION_LIMIT) < shown.index("Para enviarlo")
    assert "Los datos los recibe" not in shown  # una API: el destino ya es su URL


def test_prepare_with_codex_names_the_recipient_and_warns_of_the_shell(
    world, stages, tmp_path, monkeypatch
):
    fake_codex(tmp_path, monkeypatch)
    configure(world, provider="codex")

    prepared = world.run(*P, "prepare", stages.dossier)

    assert prepared.exit_code == 0, prepared.output
    shown = consent_part(prepared.output)
    assert "Los datos los recibe OpenAI con tu plan." in shown
    consent = shown.index("Para enviarlo")
    assert shown.index(REDACTION_LIMIT) < consent
    assert shown.index(f"Aviso: {SHELL_RISK}.") < consent


def test_a_deterministic_finding_is_shown_in_plain_language(capsys):
    rule = DeterministicRule.EVIDENCE_EXPIRED
    refs = [
        Ref(kind=RefKind.SECTION, id="7:document_not_approved:politica-seguridad:GENERATED"),
        Ref(kind=RefKind.EVIDENCE, id="EV-20261030-abc"),
    ]
    finding = Finding(
        id=finding_id(rule, "org.1", refs),
        layer=Layer.DETERMINISTIC,
        proposal=proposal_for(rule),
        measure="org.1",
        refs=refs,
        criterion=rule,
        reason="el documento politica-seguridad está obsoleto. Apruébalo de nuevo",
        source=source_for(rule),
    )

    print_finding(finding)

    shown = capsys.readouterr().out
    assert f"[SIN_CLASIFICAR] org.1 — {RULE_LABELS[rule]} (DETERMINISTIC)" in shown
    assert "  Sección: 7. Resultado por medida\n" in shown
    assert "  Evidencia: EV-20261030-abc\n" in shown
    assert f"  Fuente de la clasificación: {source_for(rule)}" in shown
    for code in (rule.value, "document_not_approved", "SECTION", "Cita "):
        assert code not in shown


def test_layer_counts_agree_in_number(world, stages):
    ran = world.run(*P, "run", stages.dossier)

    assert ran.exit_code == 0, ran.output
    [line] = [ln for ln in ran.output.splitlines() if ln.startswith("Capa determinista")]
    count = int(line.split(", ")[1].split()[0])
    assert line.endswith(f"{count} hallazgo." if count == 1 else f"{count} hallazgos.")


def test_an_observation_shows_its_own_classification_source_apart_from_the_criterion(capsys):
    refs = [Ref(kind=RefKind.DOCUMENT, id="politica-seguridad")]
    criterion = "coherencia-politica-evidencia"
    finding = Finding(
        id=finding_id(criterion, "org.1", refs),
        layer=Layer.JUDGMENT,
        proposal=Classification.OBSERVACION,
        measure="org.1",
        refs=refs,
        criterion=criterion,
        reason="La política no fija la periodicidad de revisión.",
        quote="politica-seguridad: «Se revisará periódicamente.»",
        source="CCN-CERT IC-01/19, apartado 53: «se aprecian incoherencias»",
        classification_source=judgment_classification_source(Classification.OBSERVACION),
    )

    print_finding(finding)

    shown = capsys.readouterr().out
    assert "  Fuente del criterio: CCN-CERT IC-01/19, apartado 53" in shown
    [classified] = [ln for ln in shown.splitlines() if "Fuente de la clasificación" in ln]
    assert "§VI.2, Observación" in classified and "No Conformidad Menor" not in classified


def test_discards_are_broken_down_by_reason():
    breakdown = discard_breakdown({DiscardReason.QUOTE_NOT_FOUND: 2, DiscardReason.DUPLICATE: 1})

    assert breakdown == (
        " (cita no literal o demasiado corta: 2; duplicado; se queda la clase más grave: 1)"
    )
    assert "apoyarse en el expediente" not in breakdown
    assert discard_breakdown({}) == ""


def test_a_dismissal_quotes_its_reason_once():
    refs = [Ref(kind=RefKind.DOCUMENT, id="politica-seguridad")]
    finding = Finding(
        id=finding_id("coherencia-politica-evidencia", "org.1", refs),
        layer=Layer.JUDGMENT,
        proposal=Classification.NC_MENOR,
        measure="org.1",
        refs=refs,
        criterion="coherencia-politica-evidencia",
        reason="motivo",
        quote="politica-seguridad: «cita»",
        source="fuente",
        classification_source=judgment_classification_source(Classification.NC_MENOR),
    )
    dismissed = Disposition(
        finding_id=finding.id,
        covers_run=1,
        state=DispositionState.DISMISSED,
        by="Ana Pérez",
        at=datetime(2026, 11, 4, tzinfo=UTC),
        reason="La política ya cubre ese detalle.",
    )

    line = disposition_line(Layer.JUDGMENT, FindingState(finding, (), dismissed))

    assert line.endswith("motivo: «La política ya cubre ese detalle.»; no bloquea.")
