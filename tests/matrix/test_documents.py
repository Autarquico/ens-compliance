from datetime import timedelta

import pytest
from orgdocs.orgdocs_helpers import (
    APPROVED_AT,
    EXAMPLE_SCOPE,
    SCOPE_FILES,
    SIGNATORY,
    fixture_answers,
    snapshot,
    write_answers,
)

from ens_evidence.matrix import (
    ClauseRef,
    DocumentLink,
    MatrixRowSpec,
    Obligation,
    OrgdocsSource,
    RowKind,
    default_document_source,
    documents_snapshot,
)
from ens_evidence.matrix.documents import document_support
from ens_evidence.models.enums import Role
from ens_evidence.models.readiness import BlockerKind
from ens_evidence.orgdocs import approval_history, approve_document, build_documents
from ens_evidence.orgdocs.keys import document_key
from ens_evidence.scope import approve_scope
from ens_evidence.workspace import LocalStore

from .source_helpers import ROW, SHA

DOCUMENT_APPROVED_AT = APPROVED_AT.replace(day=29)
AT = APPROVED_AT.replace(day=30)
POLICY = "politica-seguridad"
ORG_1 = tuple(f"org.1.{n}" for n in range(1, 6))
ROW_ORG_1 = MatrixRowSpec(
    "org.1",
    RowKind.SELECTED,
    (Obligation("base", tuple(ClauseRef(c, c, "texto") for c in ORG_1)),),
    SHA,
)
ROW_OTHER = MatrixRowSpec(
    "op.acc.1",
    RowKind.SELECTED,
    (Obligation("base", (ClauseRef("op.acc.1.1", "a", "texto"),)),),
    SHA,
)


@pytest.fixture
def built(tmp_path) -> LocalStore:
    workspace = LocalStore(tmp_path / "ws")
    for name in SCOPE_FILES:
        workspace.write_text(
            f"scope/{name}.yaml", (EXAMPLE_SCOPE / f"{name}.yaml").read_text(encoding="utf-8")
        )
    approve_scope(workspace, "Ana Ruiz", Role.SYSTEM_OWNER, APPROVED_AT)
    write_answers(workspace, fixture_answers())
    build_documents(workspace)
    return workspace


def approve(store, name=POLICY, role=Role.MANAGEMENT, at=DOCUMENT_APPROVED_AT):
    return approve_document(store, name, SIGNATORY[role], role, at)


def support(store, name=POLICY, *, row=ROW_ORG_1, covers=("base",), at=AT):
    snap = documents_snapshot(store, at)
    return document_support(snap, row, DocumentLink(document=name, covers=list(covers)), at)


def only_blocker(result):
    assert not result.counts
    assert result.clauses == frozenset()
    return (result.blockers[0].kind, result.blockers[0].detail)


def test_la_fuente_por_defecto_es_el_adaptador_real():
    assert isinstance(default_document_source(), OrgdocsSource)


def test_un_documento_real_aprobado_da_cobertura(built):
    approval = approve(built)

    result = support(built)

    assert result.counts
    assert result.clauses == frozenset(ORG_1)
    assert result.reviewed_document.document_sha256 == approval.document_sha256
    assert result.available_at == DOCUMENT_APPROVED_AT


def test_cuenta_la_interseccion_de_covers_con_las_clausulas_cubiertas(built):
    approve(built)

    result = support(built, covers=("org.1.2", "org.1.4"))

    assert result.clauses == frozenset({"org.1.2", "org.1.4"})


def test_clausulas_fuera_de_las_cubiertas_no_cuentan(built):
    approve(built, "designacion-responsables")

    result = support(built, "designacion-responsables")

    assert result.counts
    assert result.clauses == frozenset({"org.1.3"})


def test_un_documento_generado_sin_aprobar_no_cuenta(built):
    assert only_blocker(support(built)) == (
        BlockerKind.MISSING_EVIDENCE,
        f"document_not_approved:{POLICY}:GENERATED",
    )


def test_un_documento_no_generado_no_cuenta(tmp_path):
    empty = LocalStore(tmp_path / "vacio")

    assert only_blocker(support(empty)) == (
        BlockerKind.MISSING_EVIDENCE,
        f"document_not_approved:{POLICY}:NOT_GENERATED",
    )


def test_un_documento_obsoleto_no_cuenta(built):
    approve(built)
    answers = fixture_answers()
    answers["mision"] = "Una misión nueva y distinta."
    write_answers(built, answers)
    build_documents(built)

    assert only_blocker(support(built)) == (
        BlockerKind.EXPIRED_EVIDENCE,
        f"document_not_approved:{POLICY}:OBSOLETE",
    )


def test_un_documento_editado_a_mano_no_cuenta(built):
    approve(built)
    built.write_text(document_key(POLICY), built.read_text(document_key(POLICY)) + "\nAñadido.\n")

    result = support(built)

    assert only_blocker(result)[1] == f"document_not_approved:{POLICY}:OBSOLETE"
    entry = documents_snapshot(built, AT).documents[POLICY]
    assert "documento editado a mano" in entry.reasons


def test_un_documento_que_no_cubre_la_medida_no_la_sustenta(built):
    approve(built)

    assert only_blocker(support(built, row=ROW_OTHER)) == (
        BlockerKind.MISSING_EVIDENCE,
        f"document_not_covering:{POLICY}",
    )


def test_covers_sin_interseccion_con_lo_cubierto_no_cuenta(built):
    approve(built, "designacion-responsables")

    result = support(built, "designacion-responsables", covers=("org.1.1",))

    assert only_blocker(result)[1] == "document_not_covering:designacion-responsables"


def test_un_documento_inexistente_no_cuenta(built):
    assert only_blocker(support(built, "no-existe")) == (
        BlockerKind.MISSING_EVIDENCE,
        "document_unknown:no-existe",
    )


def test_aprobado_despues_de_la_consulta_no_cuenta(built):
    approve(built)

    result = support(built, at=DOCUMENT_APPROVED_AT - timedelta(hours=1))

    assert only_blocker(result)[1] == f"document_not_approved:{POLICY}:approved_after_query"


def test_el_sha256_es_el_de_la_aprobacion_y_cambia_si_se_reaprueba_otro_texto(built):
    first = approve(built)
    answers = fixture_answers()
    answers["mision"] = "Una misión nueva y distinta."
    write_answers(built, answers)
    build_documents(built)
    second = approve(built, at=DOCUMENT_APPROVED_AT + timedelta(hours=1))

    result = support(built)

    assert second.document_sha256 != first.document_sha256
    assert result.reviewed_document.document_sha256 == second.document_sha256
    assert approval_history(built, POLICY)[-1] == second


def test_la_instantanea_se_evalua_una_vez(built):
    snap = documents_snapshot(built, AT)
    approve(built)

    link = DocumentLink(document=POLICY, covers=["base"])

    assert document_support(snap, ROW_ORG_1, link, AT).blockers
    assert document_support(documents_snapshot(built, AT), ROW_ORG_1, link, AT).counts


def test_leer_y_aprobar_no_toca_ninguna_evaluacion(built):
    built.write_text("controls/measures.yaml", "measures: []\n")
    before = snapshot(built)

    support(built)
    approve(built)
    support(built)

    after = snapshot(built)
    assert after["controls/measures.yaml"] == before["controls/measures.yaml"]
    assert {k for k in after if k not in before} == {
        k for k in after if k.startswith(f"org/docs/approvals/{POLICY}/")
    }


def test_covers_ajeno_a_la_medida_bloquea(built):
    approve(built)

    result = support(built, row=ROW, covers=("R9",))

    assert not result.counts


def test_un_documento_aprobado_sin_huella_se_rechaza_en_vez_de_romper():
    from ens_evidence.matrix import MeasureCoverage, OrgDocumentEvidence
    from ens_evidence.matrix.documents import DocumentsSnapshot

    document = OrgDocumentEvidence(
        name=POLICY,
        state="APPROVED",
        approved_at=DOCUMENT_APPROVED_AT,
        document_sha256=None,
        measures={"org.1": MeasureCoverage(frozenset(ORG_1))},
    )
    link = DocumentLink(document=POLICY, covers=["base"])

    result = document_support(DocumentsSnapshot({POLICY: document}), ROW_ORG_1, link, AT)

    assert only_blocker(result) == (
        BlockerKind.MISSING_EVIDENCE,
        f"document_not_approved:{POLICY}:no_sha",
    )


def corrupt_latest_approval(store, name=POLICY):
    key = sorted(store.list(f"org/docs/approvals/{name}/"))[-1]
    store.write_text(key, "approved_by: [")
    return key


def test_una_aprobacion_ilegible_no_reactiva_la_anterior(built):
    approve(built)
    approve(built, at=DOCUMENT_APPROVED_AT + timedelta(hours=1))
    assert support(built).counts

    corrupt_latest_approval(built)
    result = support(built)

    assert only_blocker(result) == (
        BlockerKind.MISSING_EVIDENCE,
        f"org_document_unreadable:{POLICY}",
    )


def test_un_registro_de_aprobacion_ilegible_no_sustenta_a_otros_documentos(built):
    approve(built)
    approve(built, "designacion-responsables")
    corrupt_latest_approval(built)

    result = support(built, "designacion-responsables")

    assert result.counts


def test_el_adaptador_expone_los_registros_ilegibles(built):
    approve(built)
    key = corrupt_latest_approval(built)

    document = documents_snapshot(built, AT).documents[POLICY]

    assert document.unreadable == (key,)
    assert document.state != "APPROVED"
