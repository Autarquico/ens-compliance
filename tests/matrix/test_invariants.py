"""Regresiones de las invariantes de la matriz (reglas 2, 5 y 12 de CLAUDE.md).

Evaluación con las fuentes de producción (`OrgdocsSource`, evidencias del almacén) y, para los
checks, la fuente estática del contrato. Ninguna fuente, por verde que esté, sustituye a la
persona que revisa.
"""

import hashlib
from datetime import timedelta

import pytest

from ens_evidence.matrix import (
    MATRIX_KEY,
    REVIEWS_PREFIX,
    CheckLink,
    DocumentLink,
    ManualEvidenceLink,
    StaticCheckResults,
    assess,
    read_matrix,
    register_matrix,
    save_matrix,
)
from ens_evidence.matrix.status import row_status
from ens_evidence.models import BlockerKind, MeasureAssessment, Readiness

from .acceptance_helpers import (
    configure_measure,
    generate_and_approve_documents,
    import_manual_evidence,
    link,
    review,
)
from .evaluate_helpers import NOW, ONE_CLAUSE, TWO_CLAUSES, configure, entry, row_of
from .source_helpers import CHECK, check_result, manual_evidence, run

POLICY = "politica-seguridad"
DECLARATION_KEY = "controls/applicability.yaml"
MEASURES_KEY = "controls/measures.yaml"
FAR = NOW + timedelta(days=365)


def green_sources(store, tmp_path):
    """Una medida con evidencia manual, otra con documento aprobado y otra con un check en PASS,
    cada una con responsable y periodicidad y ninguna con revisión."""
    generate_and_approve_documents(store)
    evidence_id = manual_evidence(store, valid_until=FAR)
    org_evidence = manual_evidence(store, valid_until=FAR, data=b"acta")
    configure(
        store,
        entry(ONE_CLAUSE, ManualEvidenceLink(evidence_id=evidence_id, covers=["base"])),
        entry(
            "org.1",
            ManualEvidenceLink(evidence_id=org_evidence, covers=["org.1.1"]),
            DocumentLink(document=POLICY, covers=["org.1.2", "org.1.3", "org.1.4", "org.1.5"]),
        ),
        entry(TWO_CLAUSES, CheckLink(check_id=CHECK, covers=["base"])),
    )
    passing = run([check_result(store, evaluated_at=NOW - timedelta(hours=1))])
    return StaticCheckResults([passing])


def details(row) -> set[tuple[str, str]]:
    return {(b.kind, b.detail) for b in row.blockers}


# --- ningún camino da COMPLIANT sin una revisión humana ---------------------------------------


@pytest.fixture
def unreviewed(signed, tmp_path):
    checks = green_sources(signed, tmp_path)
    return assess(signed, NOW, checks=checks)


@pytest.mark.parametrize("measure", [ONE_CLAUSE, "org.1", TWO_CLAUSES])
def test_una_medida_con_todas_sus_fuentes_en_verde_y_sin_revision_no_esta_implantada(
    unreviewed, measure
):
    row = row_of(unreviewed, measure)

    assert row.review is None
    assert row.measure.assessment is MeasureAssessment.NOT_ASSESSED
    assert row_status(row).implemented is False
    assert (BlockerKind.MISSING_REVIEW, "review_missing") in details(row)
    assert not any(b.kind is BlockerKind.MISSING_EVIDENCE for b in row.blockers)


def test_ninguna_fila_queda_valorada_sin_que_una_persona_la_revise(unreviewed):
    assert all(r.measure.assessment is MeasureAssessment.NOT_ASSESSED for r in unreviewed.rows)
    assert not any(row_status(r).implemented for r in unreviewed.rows)
    assert unreviewed.readiness.readiness is Readiness.NOT_READY
    assert unreviewed.readiness.pending_human_review is True


def test_un_documento_aprobado_sin_revision_no_implanta_la_medida(signed):
    generate_and_approve_documents(signed)
    configure(
        signed,
        entry("org.1", DocumentLink(document=POLICY, covers=["org.1.1", "org.1.2", "org.1.3"])),
    )

    row = row_of(assess(signed, NOW), "org.1")

    assert row.supports and all(s.counts for s in row.supports)
    assert row_status(row).implemented is False
    assert (BlockerKind.MISSING_REVIEW, "review_missing") in details(row)


def test_un_check_en_verde_sin_revision_no_implanta_la_medida(signed):
    configure(signed, entry(ONE_CLAUSE, CheckLink(check_id=CHECK, covers=["base"])))
    passing = run([check_result(signed, evaluated_at=NOW - timedelta(hours=1))])

    row = row_of(assess(signed, NOW, checks=StaticCheckResults([passing])), ONE_CLAUSE)

    assert [s.counts for s in row.supports] == [True]
    assert row_status(row).implemented is False
    assert (BlockerKind.MISSING_REVIEW, "review_missing") in details(row)


def test_los_comandos_de_la_matriz_salvo_review_no_registran_revisiones(cli, signed, tmp_path):
    cli("matrix", "init")
    configure_measure(cli, ONE_CLAUSE)
    link(cli, ONE_CLAUSE, "evidence", import_manual_evidence(cli, tmp_path), "base")
    link(cli, "org.1", "document", POLICY, "org.1.1,org.1.2")

    for command in (("status",), ("export",), ("export", "--format", "md"), ("init",)):
        cli("matrix", *command)

    assert signed.list(REVIEWS_PREFIX) == []


def test_review_sin_assessment_no_registra_nada_en_una_medida_aplicable(cli, signed, tmp_path):
    cli("matrix", "init")
    configure_measure(cli, ONE_CLAUSE)
    link(cli, ONE_CLAUSE, "evidence", import_manual_evidence(cli, tmp_path), "base")

    result = cli(
        *("matrix", "review", ONE_CLAUSE, "--by", "Ana", "--role", "system_administrator"),
        "--conclusion",
        "Revisada",
    )

    assert result.exit_code != 0
    assert signed.list(REVIEWS_PREFIX) == []


# --- la matriz no toca la Declaración ---------------------------------------------------------


def sha(store, key: str) -> str:
    return hashlib.sha256(store.read_bytes(key)).hexdigest()


COMMAND_SEQUENCE = [
    ("matrix", "init"),
    ("matrix", "status"),
    ("matrix", "export"),
    ("matrix", "export", "--format", "md", "--stdout"),
    ("applicability", "show", "--annotated"),
    ("matrix", "set", ONE_CLAUSE, "--every", "60"),
    ("matrix", "unlink", ONE_CLAUSE, "--check", CHECK),  # rechazado: no estaba vinculado
    ("matrix", "review", "op.cont.3", "--by", "Ana", "--role", "system_administrator"),
    ("matrix", "review", ONE_CLAUSE, "--by", "Ana", "--role", "capitan"),  # rol rechazado
    ("matrix", "status", "--at", "2026-01-01T00:00:00+00:00"),
]


def test_tras_cualquier_secuencia_de_matrix_measures_yaml_y_la_declaracion_siguen_intactos(
    cli, signed, tmp_path, clock
):
    before = {key: sha(signed, key) for key in (MEASURES_KEY, DECLARATION_KEY)}

    cli("matrix", "init")
    configure_measure(cli, ONE_CLAUSE)
    link(cli, ONE_CLAUSE, "evidence", import_manual_evidence(cli, tmp_path), "base")
    clock.now = NOW
    assert review(cli, ONE_CLAUSE).exit_code == 0
    for command in COMMAND_SEQUENCE:
        cli(*command)

    assert {key: sha(signed, key) for key in before} == before
    status = cli("applicability", "status")
    assert status.exit_code == 0 and "CURRENT" in status.output


@pytest.mark.parametrize("command", COMMAND_SEQUENCE, ids=lambda c: " ".join(c[:3]))
def test_cada_comando_de_la_matriz_deja_intacto_todo_salvo_matriz_revisiones_e_informes(
    cli, signed, command
):
    """Lo que ya estaba en el workspace solo puede cambiar en `matrix.yaml`."""
    cli("matrix", "init")
    existing = {key: signed.read_bytes(key) for key in signed.list("")}

    cli(*command)

    changed = {key for key, data in existing.items() if signed.read_bytes(key) != data}
    assert changed <= {MATRIX_KEY}


def test_la_matriz_modificada_a_mano_no_altera_la_declaracion_al_evaluar(signed):
    configure(signed, entry(ONE_CLAUSE, CheckLink(check_id=CHECK, covers=["base"])))
    matrix, _ = read_matrix(signed)
    save_matrix(signed, matrix)
    before = {key: sha(signed, key) for key in (MEASURES_KEY, DECLARATION_KEY)}

    assess(signed, NOW)

    assert {key: sha(signed, key) for key in before} == before


# --- regla de preparación con la matriz --------------------------------------------------------


def test_una_medida_con_todo_en_verde_salvo_la_periodicidad_bloquea_la_preparacion(
    cli, signed, tmp_path, clock
):
    cli("matrix", "init")
    configure_measure(cli, "org.1", every="365")
    link(cli, "org.1", "evidence", import_manual_evidence(cli, tmp_path), "org.1.1")
    link(cli, "org.1", "document", POLICY, "org.1.2,org.1.3,org.1.4,org.1.5")
    clock.now = NOW
    assert review(cli, "org.1").exit_code == 0
    green = assess(signed, NOW)
    assert row_of(green, "org.1").blockers == ()
    assert not [b for b in green.readiness.blockers if b.measure_id == "org.1"]

    matrix, _ = read_matrix(signed)
    without_period = [
        e.model_copy(update={"review_every_days": None}) if e.id == "org.1" else e
        for e in matrix.entries
    ]
    edited = matrix.model_copy(update={"entries": without_period})
    save_matrix(signed, edited)
    register_matrix(signed, edited, NOW)
    result = assess(signed, NOW)

    blockers = {(b.kind, b.detail) for b in result.readiness.blockers if b.measure_id == "org.1"}
    assert blockers == {(BlockerKind.MISSING_REVIEW, "review_every_days_missing")}
    assert result.readiness.readiness is Readiness.NOT_READY


def test_una_aprobacion_documental_ilegible_bloquea_y_sale_en_status_y_export(
    cli, signed, tmp_path
):
    cli("matrix", "init")
    configure_measure(cli, "org.1")
    link(cli, "org.1", "evidence", import_manual_evidence(cli, tmp_path), "org.1.1")
    link(cli, "org.1", "document", POLICY, "org.1.2,org.1.3,org.1.4,org.1.5")
    reviewed = review(cli, "org.1")
    assert reviewed.exit_code == 0, reviewed.output
    key = sorted(signed.list(f"org/docs/approvals/{POLICY}/"))[-1]
    signed.write_text(key, "approved_by: [")

    status = cli("matrix", "status")
    export = cli("matrix", "export", "--format", "md")

    assert status.exit_code == 1
    assert key in status.output
    report = signed.read_text(export.output.strip())
    assert key in report
    assert "historial de aprobaciones del documento politica-seguridad" in report
