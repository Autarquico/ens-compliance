import pytest

from ens_evidence.models.enums import Role
from ens_evidence.orgdocs import approve_document, approved_coverage, build_documents
from ens_evidence.scope import revoke_approval

from .orgdocs_helpers import APPROVED_AT, SIGNATORY, fixture_answers, snapshot, write_answers

DOCUMENT_APPROVED_AT = APPROVED_AT.replace(day=29)


def approve(store, name, role=Role.MANAGEMENT):
    return approve_document(store, name, SIGNATORY[role], role, DOCUMENT_APPROVED_AT)


def test_sin_documentos_aprobados_no_hay_cobertura(built):
    trace = approved_coverage(built)

    assert trace.clauses == {}
    assert trace.measures == {}


def test_solo_cuentan_los_documentos_aprobados_y_vigentes(built):
    approval = approve(built, "politica-seguridad")
    approve(built, "normativa-seguridad")

    trace = approved_coverage(built)

    assert {"org.1.1", "org.1.5", "org.2.1", "art.2.3", "art.15.2"} <= set(trace.clauses)
    assert not {c for c in trace.clauses if c.startswith(("org.3", "org.4", "art.13.3"))}
    assert trace.clauses["org.1.1"][0].approval == approval
    assert trace.clauses["org.1.1"][0].coverage.document == "politica-seguridad"
    assert set(trace.measures) == {"org.1", "org.2"}
    assert set(trace.measures["org.1"]) == {"politica-seguridad"}


def test_la_cobertura_de_una_medida_dice_que_clausulas_cubre_y_cuales_no(built):
    approve(built, "politica-seguridad")
    approve(built, "normativa-seguridad")

    org_1 = approved_coverage(built).measures["org.1"]["politica-seguridad"]

    assert {"org.1.1", "org.1.2", "org.1.3", "org.1.4", "org.1.5"} <= set(org_1.covered_clauses)
    assert org_1.missing_clauses == []


def test_una_medida_con_dos_documentos_aprobados_los_lista_a_los_dos(built):
    approve(built, "politica-seguridad")
    approve(built, "designacion-responsables")

    documents = approved_coverage(built).measures["org.1"]

    assert set(documents) == {"politica-seguridad", "designacion-responsables"}
    assert documents["designacion-responsables"].covered_clauses == ["org.1.3"]
    assert "org.1.1" in documents["designacion-responsables"].missing_clauses


def test_un_documento_aprobado_pero_obsoleto_deja_de_contar(built):
    approve(built, "politica-seguridad")
    approve(built, "normativa-seguridad")
    answers = fixture_answers()
    answers["mision"] = "Una misión nueva y distinta."
    write_answers(built, answers)
    build_documents(built)

    trace = approved_coverage(built)

    assert set(trace.measures) == {"org.2"}
    assert "org.1.1" not in trace.clauses


def test_un_alcance_revocado_deja_sin_cobertura_a_todos_los_documentos(built):
    approve(built, "politica-seguridad")
    revoke_approval(built, "prueba", DOCUMENT_APPROVED_AT)

    assert approved_coverage(built).clauses == {}


@pytest.mark.parametrize(
    ("name", "role"),
    [("politica-seguridad", Role.MANAGEMENT), ("procedimientos-seguridad", Role.SECURITY_OFFICER)],
)
def test_aprobar_un_documento_solo_añade_su_registro_y_no_toca_ninguna_evaluacion(
    built, name, role
):
    built.write_text("controls/measures.yaml", "measures: []\n")
    before = snapshot(built)

    approve(built, name, role)

    after = snapshot(built)
    assert {k for k in after if k not in before} == {
        k for k in after if k.startswith(f"org/docs/approvals/{name}/")
    }
    assert {k: v for k, v in after.items() if k in before} == before
