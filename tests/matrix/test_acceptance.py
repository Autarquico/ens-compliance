"""Los tres escenarios de `docs/specs/compliance-matrix.md`, por la CLI y de punta a punta.

Workspace real: alcance aprobado, categorización, Declaración BÁSICA firmada y los org-docs
reales generados y aprobados. Sin fuentes falsas: `OrgdocsSource` es el de producción.
"""

import json
from datetime import timedelta

import yaml

from .acceptance_helpers import (
    configure_measure,
    import_manual_evidence,
    line_of,
    link,
    review,
)
from .evaluate_helpers import NOW, ONE_CLAUSE

POLICY = "politica-seguridad"
ORGANIZATIONAL = "org.1"
POLICY_CLAUSES = "org.1.2,org.1.3,org.1.4,org.1.5"


def export(run) -> dict:
    result = run("matrix", "export", "--stdout")
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def row(document: dict, measure: str) -> dict:
    return next(r for r in document["rows"] if r["measure_id"] == measure)


def blocker_details(document_row: dict) -> set[tuple[str, str]]:
    return {(b["kind"], b["detail"]) for b in document_row["blockers"]}


def implant_organizational_measure(run, tmp_path, clock) -> str:
    """Escenario 2: evidencia manual importada por la CLI, un documento aprobado y la revisión."""
    assert run("matrix", "init").exit_code == 0
    configure_measure(run, ORGANIZATIONAL, every="365")
    evidence_id = import_manual_evidence(run, tmp_path)
    link(run, ORGANIZATIONAL, "evidence", evidence_id, "org.1.1")
    link(run, ORGANIZATIONAL, "document", POLICY, POLICY_CLAUSES)
    clock.now = NOW
    reviewed = review(run, ORGANIZATIONAL)
    assert reviewed.exit_code == 0, reviewed.output
    return evidence_id


# --- escenario 1: medida sin revisión ---------------------------------------------------------


def test_una_medida_con_evidencia_y_sin_revision_aparece_como_bloqueo(cli, tmp_path, clock):
    assert cli("matrix", "init").exit_code == 0
    configure_measure(cli, ONE_CLAUSE)
    evidence_id = import_manual_evidence(cli, tmp_path)
    link(cli, ONE_CLAUSE, "evidence", evidence_id, "base")
    clock.now = NOW

    status = cli("matrix", "status", ONE_CLAUSE)
    exported = row(export(cli), ONE_CLAUSE)

    assert status.exit_code == 1
    assert "implantada: no" in line_of(status.output, ONE_CLAUSE)
    assert "bloqueo: MISSING_REVIEW review_missing" in status.output
    assert ("MISSING_REVIEW", "review_missing") in blocker_details(exported)
    assert exported["manual"][0]["counts"] is True  # la evidencia es válida: falta la persona
    assert exported["last_review"] is None and exported["implemented"] is False


def test_el_bloqueo_sin_revision_entra_en_la_preparacion_global(cli, tmp_path, clock):
    cli("matrix", "init")
    configure_measure(cli, ONE_CLAUSE)
    link(cli, ONE_CLAUSE, "evidence", import_manual_evidence(cli, tmp_path), "base")
    clock.now = NOW

    document = export(cli)

    assert document["readiness"] == "NOT_READY"
    assert {"kind": "MISSING_REVIEW", "measure_id": ONE_CLAUSE, "detail": "review_missing"} in (
        document["blockers"]
    )


# --- escenario 2: evidencia manual ------------------------------------------------------------


def test_evidencia_manual_mas_documento_aprobado_y_revision_dan_la_medida_implantada(
    cli, tmp_path, clock
):
    evidence_id = implant_organizational_measure(cli, tmp_path, clock)

    status = cli("matrix", "status", ORGANIZATIONAL)
    exported = row(export(cli), ORGANIZATIONAL)

    line = line_of(status.output, ORGANIZATIONAL)
    assert status.exit_code == 0, status.output
    assert "implantada: sí" in line and "revisión regular: sí" in line
    assert f"evidencia {evidence_id}" in line and f"documento {POLICY}" in line
    assert exported["implemented"] is True and exported["review_current"] is True
    assert exported["blockers"] == []
    assert exported["last_review"]["assessment"] == "COMPLIANT"
    assert exported["last_review"]["reviewer"] == "Ana"
    assert {(m["kind"], m["target"], m["counts"]) for m in exported["manual"]} == {
        ("evidence", evidence_id, True),
        ("org_document", POLICY, True),
    }


def test_el_documento_aprobado_solo_no_cubre_la_clausula_de_la_evidencia_manual(
    cli, tmp_path, clock
):
    cli("matrix", "init")
    configure_measure(cli, ORGANIZATIONAL, every="365")
    link(cli, ORGANIZATIONAL, "document", POLICY, POLICY_CLAUSES)
    clock.now = NOW

    rejected = review(cli, ORGANIZATIONAL)

    assert rejected.exit_code == 1
    assert "cláusula sin cubrir: org.1.1" in rejected.output


def test_la_evidencia_manual_importada_tras_la_revision_no_la_sustenta(cli, tmp_path, clock):
    cli("matrix", "init")
    configure_measure(cli, ONE_CLAUSE)
    first = import_manual_evidence(cli, tmp_path)
    link(cli, ONE_CLAUSE, "evidence", first, "base")
    clock.now = NOW
    assert review(cli, ONE_CLAUSE).exit_code == 0
    clock.now = NOW + timedelta(hours=1)
    second = import_manual_evidence(cli, tmp_path, content="otra acta")
    link(cli, ONE_CLAUSE, "evidence", second, "base")

    exported = row(export(cli), ONE_CLAUSE)

    assert exported["implemented"] is False
    assert ("EXPIRED_REVIEW", "links_changed") in blocker_details(exported)


# --- escenario 3: cobertura completa ----------------------------------------------------------


def declaration_ids(workspace) -> dict[str, set[str]]:
    declaration = yaml.safe_load(workspace.read_text("controls/applicability.yaml"))
    return {
        "selected": {m["id"] for m in declaration["selected"]},
        "not_selected": {m["id"] for m in declaration["not_selected"]},
        "absent_assets": {m["id"] for m in declaration["required_but_absent_assets"]},
    }


def test_el_export_json_cubre_exactamente_las_medidas_de_la_declaracion(cli, signed):
    assert cli("matrix", "init").exit_code == 0
    expected = declaration_ids(signed)

    document = export(cli)

    ids = [r["measure_id"] for r in document["rows"]]
    assert len(ids) == len(set(ids))
    assert set(ids) == expected["selected"] | expected["not_selected"] | expected["absent_assets"]
    for kind, measures in expected.items():
        assert {r["measure_id"] for r in document["rows"] if r["kind"] == kind} == measures
    assert expected["selected"] >= {"op.pl.1", "op.cont.2"}  # la compensada y la adicional


def test_el_export_guardado_con_formato_json_es_el_mismo_que_el_de_stdout(cli, signed):
    cli("matrix", "init")
    key = cli("matrix", "export", "--format", "json").output.strip()

    stored = json.loads(signed.read_text(key))

    assert stored == export(cli)


def test_el_export_md_nombra_todas_las_medidas_aplicables(cli, signed):
    cli("matrix", "init")
    selected = declaration_ids(signed)["selected"]

    text = cli("matrix", "export", "--format", "md", "--stdout").output

    assert {m for m in selected if m in text} == selected


def test_sin_matrix_init_el_export_sigue_cubriendo_la_declaracion(cli, signed):
    """Las filas salen de la Declaración, no de `matrix.yaml`: sin entradas hay bloqueos."""
    expected = declaration_ids(signed)

    document = export(cli)

    assert {r["measure_id"] for r in document["rows"]} == set().union(*expected.values())
    assert all(r["blockers"] for r in document["rows"])


# --- el almacén S3 falso ----------------------------------------------------------------------


def test_matrix_export_sobre_s3_guarda_la_copia_y_cubre_la_declaracion(s3_cli, signed):
    run, remote = s3_cli
    assert run("matrix", "init").exit_code == 0

    result = run("matrix", "export")

    key = result.output.strip()
    assert result.exit_code == 0, result.output
    assert key.startswith("evidence/reports/matrix/") and key.endswith(".json")
    assert remote.list("evidence/reports/matrix") == [key]
    stored = json.loads(remote.read_text(key))
    expected = declaration_ids(signed)
    assert {r["measure_id"] for r in stored["rows"]} == set().union(*expected.values())


def test_el_escenario_2_de_punta_a_punta_sobre_s3(s3_cli, tmp_path, clock):
    run, remote = s3_cli
    evidence_id = implant_organizational_measure(run, tmp_path, clock)

    key = run("matrix", "export").output.strip()

    stored = json.loads(remote.read_text(key))
    exported = row(stored, ORGANIZATIONAL)
    assert exported["implemented"] is True and exported["blockers"] == []
    assert evidence_id in {e["id"] for e in stored["sources"]["evidence"]}
    assert POLICY in {d["name"] for d in stored["sources"]["documents"]}


def test_el_export_sobre_s3_no_sobrescribe_una_copia_anterior(s3_cli):
    run, remote = s3_cli
    run("matrix", "init")

    first = run("matrix", "export")
    second = run("matrix", "export")

    assert first.exit_code == 0
    assert second.exit_code == 2
    assert remote.list("evidence/reports/matrix") == [first.output.strip()]
