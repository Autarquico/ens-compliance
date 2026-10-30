import pytest
import yaml

from ens_evidence.models.enums import Role
from ens_evidence.orgdocs import (
    DocumentApprovalRejected,
    DocumentState,
    approve_document,
    approved_coverage,
    build_documents,
    document_status,
)
from ens_evidence.orgdocs.document import sha256_text

from .orgdocs_helpers import (
    APPROVED_AT,
    SIGNATORY,
    fixture_answers,
    traceability_rows,
    write_answers,
)

AT = APPROVED_AT.replace(day=29)
POLICY = "politica-seguridad"
POC_SECTION = "Punto de contacto de seguridad"


def build_with(store, **changes):
    answers = fixture_answers()
    answers.update(changes)
    write_answers(store, answers)
    build_documents(store)


def approve(store, name):
    return approve_document(store, name, SIGNATORY[Role.MANAGEMENT], Role.MANAGEMENT, AT)


def forge(store, name, extra):
    """Edita el documento y deja el meta coherente con la edición."""
    text = store.read_text(f"org/docs/{name}.md") + extra
    meta = yaml.safe_load(store.read_text(f"org/docs/{name}.meta.yaml"))
    meta["document_sha256"] = sha256_text(text)
    store.write_text(f"org/docs/{name}.md", text)
    store.write_text(f"org/docs/{name}.meta.yaml", yaml.safe_dump(meta, allow_unicode=True))


def test_la_trazabilidad_no_cita_secciones_condicionales_omitidas(answered):
    build_with(answered, servicios_externalizados="no")

    document = answered.read_text(f"org/docs/{POLICY}.md")
    meta = yaml.safe_load(answered.read_text(f"org/docs/{POLICY}.meta.yaml"))

    assert f"## {POC_SECTION}" not in document
    sections = {row[2] for row in traceability_rows(document)}
    assert POC_SECTION not in sections
    assert "poc" not in {c["section"] for c in meta["clauses"]}
    assert all(f"## {title}\n" in document for title in sections)


def test_la_trazabilidad_cita_la_seccion_condicional_cuando_se_incluye(answered):
    build_with(answered, servicios_externalizados="sí")

    document = answered.read_text(f"org/docs/{POLICY}.md")

    assert f"## {POC_SECTION}\n" in document
    assert POC_SECTION in {row[2] for row in traceability_rows(document)}


def test_la_cobertura_aprobada_no_incluye_secciones_omitidas(answered):
    build_with(answered, servicios_externalizados="no")
    approve(answered, POLICY)

    covered = {
        c.coverage.section for cs in approved_coverage(answered).clauses.values() for c in cs
    }

    assert "poc" not in covered


def test_un_documento_editado_con_su_meta_coherente_no_se_aprueba(built):
    forge(built, POLICY, "\nTexto añadido a mano que no sale de las respuestas.\n")

    status = document_status(built, POLICY)
    with pytest.raises(DocumentApprovalRejected, match="editado a mano"):
        approve(built, POLICY)

    assert status.state is DocumentState.GENERATED
    assert status.reasons == ["documento editado a mano"]
    assert built.list(f"org/docs/approvals/{POLICY}/") == []


def test_un_documento_aprobado_y_editado_con_el_meta_forzado_queda_obsoleto(built):
    approve(built, POLICY)
    forge(built, POLICY, "\nTexto añadido a mano.\n")

    status = document_status(built, POLICY)

    assert status.state is DocumentState.OBSOLETE
    assert "documento editado a mano" in status.reasons


def test_un_valor_mal_formado_en_una_respuesta_ajena_no_invalida_un_documento(built):
    approve(built, POLICY)
    answers = fixture_answers()
    answers["medidas_disciplinarias"] = 42
    write_answers(built, answers)

    assert document_status(built, POLICY).state is DocumentState.APPROVED
    assert document_status(built, "normativa-seguridad").state is DocumentState.GENERATED


def test_un_problema_en_una_respuesta_propia_si_invalida_el_documento(built):
    approve(built, POLICY)
    answers = fixture_answers()
    answers["mision"] = 42
    write_answers(built, answers)

    assert document_status(built, POLICY).state is DocumentState.OBSOLETE


def test_un_fichero_de_respuestas_ilegible_invalida_todos_los_documentos(built):
    approve(built, POLICY)
    built.write_text("org/answers.yaml", "[: no es yaml")

    assert document_status(built, POLICY).state is DocumentState.OBSOLETE


def test_un_valor_ajeno_mal_formado_no_impide_aprobar_otro_documento(built):
    answers = fixture_answers()
    answers["medidas_disciplinarias"] = 42
    write_answers(built, answers)

    approve(built, POLICY)


def test_cada_registro_con_encabezado_va_separado_del_siguiente(built):
    document = built.read_text("org/docs/procedimientos-seguridad.md")

    lines = document.splitlines()
    for number, line in enumerate(lines):
        if line.startswith("### ") and number:
            assert lines[number - 1] == "", line
