import hashlib
import json
import re
from dataclasses import replace
from datetime import timedelta

import pytest
from applicability.helpers import LATER, decide_all_undetermined, prepared_workspace, uniform

from ens_evidence.applicability import approve_declaration, propose_applicability
from ens_evidence.evidence.layout import manifest_key
from ens_evidence.evidence.manifests import load_manifest_by_id
from ens_evidence.matrix import (
    CheckLink,
    DocumentLink,
    ExportRejected,
    ManualEvidenceLink,
    MatrixEntry,
    Owner,
    StaticCheckResults,
    assess,
    build_export,
    prepare_review,
    read_reviews,
    record_review,
    render_json,
    render_markdown,
    save_report,
)
from ens_evidence.models.enums import Category, Role

from .evaluate_helpers import (
    NOW,
    ONE_CLAUSE,
    TWO_CLAUSES,
    FakeDocuments,
    approved_document,
    configure,
    entry,
    record,
)
from .helpers import SIGNED_AT
from .source_helpers import CHECK, check_result, manual_evidence, run

GENERATED = NOW + timedelta(minutes=5)
DOCUMENT = "politica"


@pytest.fixture
def basic(tmp_path):
    """Declaración real de categoría BÁSICA: todas las medidas decididas y firmada."""
    store = prepared_workspace(tmp_path / "basic", uniform("BAJO"))
    propose_applicability(store, LATER)
    decide_all_undetermined(store)
    approve_declaration(store, "Luis Pardo", SIGNED_AT)
    return store


def evaluate(store, checks=(), documents=()):
    return assess(
        store, NOW, checks=StaticCheckResults(checks), documents=FakeDocuments(*documents)
    )


def export_of(store, **sources):
    return build_export(store, evaluate(store, **sources), generated_at=GENERATED)


@pytest.fixture
def sourced(signed):
    """Dos medidas revisadas: evidencia y check en una; evidencia y documento en otra."""
    manual = manual_evidence(signed, valid_until=NOW + timedelta(days=365))
    other = manual_evidence(signed, valid_until=NOW + timedelta(days=365), data=b"otra")
    passing = check_result(signed, evaluated_at=NOW - timedelta(hours=14))
    finished = NOW - timedelta(hours=14)
    first = entry(
        ONE_CLAUSE,
        ManualEvidenceLink(evidence_id=manual, covers=["base"]),
        CheckLink(check_id=CHECK, covers=["base"]),
    )
    second = entry(
        TWO_CLAUSES,
        ManualEvidenceLink(evidence_id=other, covers=["op.ext.4.1"]),
        DocumentLink(document=DOCUMENT, covers=["op.ext.4.2"]),
    )
    configure(signed, first, second)
    record(signed, first, evidence_ids=[manual], checks=[CHECK])
    record(signed, second, evidence_ids=[other], documents=[DOCUMENT])
    return {
        "store": signed,
        "manual": manual,
        "other": other,
        "check_evidence": passing.evidence_ids,
        "checks": [run([passing], finished_at=finished)],
        "documents": [approved_document(DOCUMENT)],
    }


def sourced_export(sourced):
    return export_of(sourced["store"], checks=sourced["checks"], documents=sourced["documents"])


# --- escenario 3: cobertura completa con la Declaración BÁSICA real ----------------------------


def test_el_export_de_la_declaracion_basica_real_tiene_exactamente_sus_filas(basic):
    from ens_evidence.applicability import declaration_status

    declaration = declaration_status(basic, NOW).declaration
    expected = [
        *(m.id for m in declaration.selected),
        *(m.id for m in declaration.not_selected),
        *(m.id for m in declaration.required_but_absent_assets),
    ]

    export = export_of(basic)

    ids = [r.measure_id for r in export.rows]
    assert export.declaration.category is Category.BASICA
    assert len(ids) == len(set(ids)) == len(expected) > 0
    assert set(ids) == set(expected)
    assert {r.kind.value for r in export.rows} >= {"selected", "not_selected"}


def test_el_export_falla_si_falta_una_fila_de_la_declaracion(basic):
    evaluation = evaluate(basic)
    missing = evaluation.rows[0].spec.measure_id

    with pytest.raises(ExportRejected) as raised:
        build_export(basic, replace(evaluation, rows=evaluation.rows[1:]), generated_at=GENERATED)

    assert raised.value.errors == [f"{missing}: falta la fila de la Declaración."]


def test_el_export_falla_si_hay_una_fila_duplicada_o_ajena(basic):
    evaluation = evaluate(basic)
    first = evaluation.rows[0]

    with pytest.raises(ExportRejected, match="duplicada"):
        build_export(
            basic, replace(evaluation, rows=(*evaluation.rows, first)), generated_at=GENERATED
        )

    alien = replace(first, spec=replace(first.spec, measure_id="op.zzz.9"))
    with pytest.raises(ExportRejected, match="op.zzz.9: fila que no está"):
        build_export(
            basic, replace(evaluation, rows=(*evaluation.rows, alien)), generated_at=GENERATED
        )


def test_el_export_sin_declaracion_firmada_se_rechaza(ready):
    with pytest.raises(ExportRejected, match="NOT_APPROVED"):
        build_export(ready, evaluate(ready), generated_at=GENERATED)


def test_el_export_con_la_declaracion_obsoleta_la_marca_y_bloquea_todo(signed):
    key = "controls/measures.yaml"
    signed.write_text(key, signed.read_text(key) + "\n")

    export = export_of(signed)

    assert export.declaration.state.value == "STALE"
    assert any(b.detail == "declaration_stale" for b in export.blockers)
    assert all(
        any(b.kind.value == "UNDETERMINED_APPLICABILITY" for b in r.blockers) for r in export.rows
    )


# --- fuentes y timestamps ---------------------------------------------------------------------


def test_el_export_lleva_marcas_de_tiempo_versiones_y_datos_de_la_declaracion(sourced):
    export = sourced_export(sourced)

    assert export.generated_at == GENERATED and export.evaluated_at == NOW
    assert export.product_version and export.catalog_version.snapshot_sha256
    declaration = export.declaration
    assert (declaration.approved_by, declaration.role) == ("Luis Pardo", Role.SECURITY_OFFICER)
    assert declaration.approved_at == SIGNED_AT and declaration.state.value == "CURRENT"
    assert declaration.measures_sha256 and declaration.scope_fingerprint
    assert re.fullmatch(r"[0-9a-f]{64}", export.matrix_sha256)
    assert export.disclaimer.endswith("la valoración es de la persona que revisa.")


def test_el_export_sin_matriz_lo_dice_en_vez_de_inventar_una_huella(signed):
    assert export_of(signed).matrix_sha256 is None


def test_las_fuentes_por_evidencia_llevan_manifiesto_hash_procedencia_y_fechas(sourced):
    store = sourced["store"]

    export = sourced_export(sourced)

    by_id = {e.id: e for e in export.sources.evidence}
    assert set(by_id) == {sourced["manual"], sourced["other"], *sourced["check_evidence"]}
    manual = by_id[sourced["manual"]]
    manifest = load_manifest_by_id(store, manual.id)
    assert manual.manifest_key == manifest_key(manual.id)
    assert manual.artifact_sha256 == manifest.artifact.sha256
    assert manual.provenance == manifest.provenance
    assert (manual.collected_at, manual.valid_until) == (
        manifest.collected_at,
        manifest.valid_until,
    )
    assert manual.provenance.submitted_by == "Ana"


def test_las_fuentes_por_check_documento_y_revision(sourced):
    store = sourced["store"]

    export = sourced_export(sourced)

    (check,) = export.sources.checks
    assert (check.check_id, check.connector, check.measures) == (CHECK, "github", [ONE_CLAUSE])
    assert check.last_run_at == NOW - timedelta(hours=14)
    assert check.evidence_ids == sorted(sourced["check_evidence"])
    (document,) = export.sources.documents
    assert (document.name, document.measures) == (DOCUMENT, [TWO_CLAUSES])
    assert document.approved_at == NOW - timedelta(days=2) and document.document_sha256
    keys = {m: read_reviews(store, m)[0][0].key for m in (ONE_CLAUSE, TWO_CLAUSES)}
    assert {(r.measure_id, r.key) for r in export.sources.reviews} == set(keys.items())


def test_la_fila_revisada_lleva_enlaces_responsable_revision_e_implantada(sourced):
    export = sourced_export(sourced)

    row = next(r for r in export.rows if r.measure_id == ONE_CLAUSE)
    assert row.implemented and row.review_current and row.blockers == []
    assert [(link.target, link.counts) for link in row.technical] == [(CHECK, True)]
    assert [link.target for link in row.manual] == [sourced["manual"]]
    assert row.owner.name == "Ana" and row.review_every_days == 30
    assert row.last_review.reviewer == "Ana" and row.last_review.assessment.value == "COMPLIANT"
    assert row.name


def test_un_enlace_que_no_cuenta_se_marca_en_la_fila(sourced):
    export = export_of(sourced["store"], checks=[], documents=sourced["documents"])

    row = next(r for r in export.rows if r.measure_id == ONE_CLAUSE)
    assert [(link.target, link.counts) for link in row.technical] == [(CHECK, False)]
    assert not row.implemented
    assert any(b.detail == f"check_no_results:{CHECK}" for b in row.blockers)


# --- determinismo -----------------------------------------------------------------------------


def test_mismas_entradas_y_mismo_generated_at_dan_los_mismos_bytes(sourced):
    first = sourced_export(sourced)
    second = sourced_export(sourced)

    assert render_json(first).encode() == render_json(second).encode()
    assert render_markdown(first).encode() == render_markdown(second).encode()


def test_otro_generated_at_cambia_solo_la_marca(sourced):
    first = sourced_export(sourced)
    later = first.model_copy(update={"generated_at": GENERATED + timedelta(seconds=1)})

    a, b = json.loads(render_json(first)), json.loads(render_json(later))
    assert a.pop("generated_at") != b.pop("generated_at")
    assert a == b


def test_el_json_es_valido_contra_el_modelo(sourced):
    from ens_evidence.matrix import MatrixExport

    export = sourced_export(sourced)

    assert MatrixExport.model_validate_json(render_json(export)) == export


# --- Markdown ---------------------------------------------------------------------------------

COLUMNS = [
    "Medida",
    "Requisitos (cláusulas)",
    "Evidencias técnicas",
    "Evidencias manuales y documentos",
    "Responsable",
    "Periodicidad",
    "Última revisión",
    "¿Implantada?",
    "¿Revisión vigente?",
    "Bloqueos",
]


def cells(line: str) -> list[str]:
    return [c.strip() for c in re.split(r"(?<!\\)\|", line)[1:-1]]


def test_la_tabla_markdown_tiene_las_columnas_pedidas_y_una_fila_por_medida(sourced):
    export = sourced_export(sourced)

    lines = render_markdown(export).splitlines()

    header = next(i for i, line in enumerate(lines) if line.startswith("| Medida |"))
    assert cells(lines[header]) == COLUMNS
    table = [line for line in lines[header + 2 :] if line.startswith("| ")]
    assert len(table) == len(export.rows)
    assert all(len(cells(line)) == len(COLUMNS) for line in table)
    revised = next(line for line in table if line.startswith(f"| {ONE_CLAUSE} "))
    row = cells(revised)
    assert row[2] == CHECK.replace("_", r"\_") + " (cubre base)"
    assert row[7] == "sí" and row[8] == "sí"
    assert "Ana (administrador del sistema)" in row[6]


def test_el_markdown_termina_con_las_fuentes_y_el_aviso(sourced):
    export = sourced_export(sourced)

    text = render_markdown(export)

    assert text.index("## Fuentes") < text.index("### Revisiones")
    assert f"manifiesto `{manifest_key(sourced['manual'])}`" in text
    assert "2026-10-30T12:05:00+00:00" in text
    assert text.rstrip().endswith(
        "La herramienta no declara conformidad; la valoración es de la persona que revisa."
    )
    assert "cumple" not in text.lower()


def test_el_markdown_escapa_los_textos_libres(signed):
    hostile = "Ana|B *x* _y_ [l](http://e) <b>z</b>\n# titulo"
    configure(
        signed,
        MatrixEntry(id=ONE_CLAUSE, owner=Owner(name=hostile, role=Role.SYSTEM_ADMINISTRATOR)),
    )

    text = render_markdown(export_of(signed))

    line = next(line for line in text.splitlines() if line.startswith(f"| {ONE_CLAUSE} "))
    assert len(cells(line)) == len(COLUMNS)
    owner = cells(line)[4]
    assert owner == (
        r"Ana\|B \*x\* \_y\_ \[l\](http://e) \<b\>z\</b\> \# titulo (administrador del sistema)"
    )
    assert "<b>" not in text


def test_los_bloqueos_globales_salen_en_el_markdown(signed):
    key = "controls/measures.yaml"
    signed.write_text(key, signed.read_text(key) + "\n")

    text = render_markdown(export_of(signed))

    assert "- global: la Declaración de Aplicabilidad ya no vale" in text
    assert "todas las medidas quedan bloqueadas" in text


# --- almacenamiento ---------------------------------------------------------------------------


def test_save_report_escribe_una_copia_nueva_y_no_sobrescribe(signed):
    key = save_report(signed, "evidence/reports/matrix", GENERATED, "json", "uno")

    assert key == "evidence/reports/matrix/20261030T120500000000Z.json"
    assert signed.read_text(key) == "uno"
    with pytest.raises(Exception, match="exist"):
        save_report(signed, "evidence/reports/matrix", GENERATED, "json", "dos")
    assert signed.read_text(key) == "uno"


# --- ronda 1: conclusión y cobertura, orden, textos --------------------------------------------


@pytest.fixture
def reviewed(signed):
    """ONE_CLAUSE revisada por `prepare_review`, que es quien atribuye la cobertura por cláusula."""
    evidence_id = manual_evidence(signed, valid_until=NOW + timedelta(days=365))
    configure(
        signed, entry(ONE_CLAUSE, ManualEvidenceLink(evidence_id=evidence_id, covers=["base"]))
    )
    plan = prepare_review(
        signed,
        NOW,
        ONE_CLAUSE,
        by="Ana",
        role="system_administrator",
        conclusion="Inventario completo y contrastado",
        assessment="COMPLIANT",
        documents=FakeDocuments(),
    )
    assert not plan.problems, plan.problems
    key = record_review(signed, plan)
    return signed, evidence_id, key


def exported_row(export, measure_id):
    return next(r for r in export.rows if r.measure_id == measure_id)


def test_el_export_lleva_la_conclusion_la_huella_del_registro_y_la_cobertura_confirmada(reviewed):
    store, evidence_id, key = reviewed

    review = exported_row(export_of(store), ONE_CLAUSE).last_review

    assert review.conclusion == "Inventario completo y contrastado"
    assert review.record_sha256 == hashlib.sha256(store.read_bytes(key)).hexdigest()
    (obligation,) = review.obligations
    assert [(c.clause, c.sources) for c in obligation.coverage] == [
        ("op.exp.1.1", [f"evidence:{evidence_id}"])
    ]
    assert json.loads(render_json(export_of(store)))["rows"]


def test_el_markdown_muestra_la_conclusion_y_cada_clausula_con_su_fuente(reviewed):
    store, evidence_id, key = reviewed

    text = render_markdown(export_of(store))

    revisions = text[text.index("### Revisiones") :]
    assert "conclusión: Inventario completo y contrastado" in revisions
    assert f"cláusula op.exp.1.1 ← evidence:{evidence_id}" in revisions
    assert f"registro `{key}`" in revisions
    assert "sha256 `" in revisions


def test_el_markdown_muestra_que_cubre_cada_enlace(reviewed):
    store, evidence_id, _ = reviewed

    text = render_markdown(export_of(store))

    line = next(line for line in text.splitlines() if line.startswith(f"| {ONE_CLAUSE} "))
    assert f"{evidence_id} (cubre base)" in line


def test_las_filas_salen_en_el_orden_del_catalogo_y_no_alfabetico(basic):
    from ens_evidence.catalog import load_catalog

    order = [m.id for m in load_catalog().measures]

    ids = [r.measure_id for r in export_of(basic).rows]

    assert ids == sorted(ids, key=order.index)
    assert ids != sorted(ids)


def test_el_json_conserva_los_codigos_y_el_markdown_los_traduce(signed):
    export = export_of(signed)

    document = json.loads(render_json(export))
    text = render_markdown(export)

    row = next(r for r in document["rows"] if r["measure_id"] == ONE_CLAUSE)
    assert {"kind": "MISSING_REVIEW", "measure_id": ONE_CLAUSE, "detail": "review_missing"} in (
        row["blockers"]
    )
    assert "MISSING\\_REVIEW" not in text and "NOT\\_COMPLIANT" not in text
    assert "sin revisar: nadie ha valorado esta medida todavía" in text
    assert "matrix review" in text
    assert "no conforme" not in text


def test_las_cifras_del_markdown_van_en_singular_y_sin_caducidad_se_explica(reviewed):
    store, _, _ = reviewed
    export = export_of(store)
    one = export.model_copy(update={"blockers": export.blockers[:1]})
    none_expiring = export.model_copy(
        update={
            "sources": export.sources.model_copy(
                update={
                    "evidence": [
                        e.model_copy(update={"valid_until": None}) for e in export.sources.evidence
                    ]
                }
            )
        }
    )

    assert "- Preparación: NOT_READY, 1 bloqueo\n" in render_markdown(one)
    assert "1 bloqueos" not in render_markdown(one)
    assert "cuenta dentro de la periodicidad de la medida" in render_markdown(none_expiring)


def test_un_hueco_de_cobertura_agrupa_las_clausulas_en_una_linea(signed):
    configure(signed, entry(TWO_CLAUSES))

    text = render_markdown(export_of(signed))

    line = next(line for line in text.splitlines() if line.startswith(f"| {TWO_CLAUSES} "))
    assert "faltan evidencias en 2 cláusulas: op.ext.4.1, op.ext.4.2" in line
