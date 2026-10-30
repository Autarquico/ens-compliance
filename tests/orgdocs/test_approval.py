import hashlib
from datetime import timedelta

import pytest
import yaml

from ens_evidence.models.enums import Role
from ens_evidence.orgdocs import (
    DocumentApprovalRejected,
    DocumentState,
    UnknownDocument,
    approval_history,
    approve_document,
    build_documents,
    document_status,
)
from ens_evidence.scope import ScopeNotApproved, revoke_approval

from .orgdocs_helpers import APPROVED_AT, DOCUMENTS, ens, fixture_answers, write_answers

DOCUMENT_APPROVED_AT = APPROVED_AT + timedelta(days=1)
MANAGEMENT_ONLY = ["politica-seguridad", "normativa-seguridad", "designacion-responsables"]
DECLARED_AUTHORITY = ["procedimientos-seguridad", "proceso-autorizacion"]


def approval_keys(store, name):
    return store.list(f"org/docs/approvals/{name}/")


def test_la_aprobacion_registra_persona_rol_fecha_con_zona_y_sha256_real(built):
    document = built.read_bytes("org/docs/politica-seguridad.md")

    approve_document(
        built, "politica-seguridad", "Marta Ibáñez Soler", Role.MANAGEMENT, DOCUMENT_APPROVED_AT
    )

    (key,) = approval_keys(built, "politica-seguridad")
    record = yaml.safe_load(built.read_text(key))
    assert record["approved_by"] == "Marta Ibáñez Soler"
    assert record["role"] == "management"
    assert record["approved_at"] == "2026-10-29T09:30:00Z"
    assert approval_history(built, "politica-seguridad")[0].approved_at == DOCUMENT_APPROVED_AT
    assert record["document_sha256"] == hashlib.sha256(document).hexdigest()


def test_docs_approve_por_cli_registra_y_muestra_el_hash(built, settings):
    document = built.read_bytes("org/docs/politica-seguridad.md")

    result = ens(
        settings,
        *(
            "docs",
            "approve",
            "politica-seguridad",
            "--by",
            "Marta Ibáñez Soler",
            "--role",
            "management",
        ),
    )

    assert result.exit_code == 0, result.output
    assert hashlib.sha256(document).hexdigest() in result.output
    (approval,) = approval_history(built, "politica-seguridad")
    assert approval.approved_by == "Marta Ibáñez Soler"
    assert approval.approved_at.utcoffset() is not None


@pytest.mark.parametrize("name", MANAGEMENT_ONLY)
def test_politica_normativa_y_designacion_solo_las_aprueba_la_direccion(built, name):
    for role in (Role.SECURITY_OFFICER, Role.SYSTEM_OWNER, Role.SYSTEM_ADMINISTRATOR):
        with pytest.raises(DocumentApprovalRejected, match="lo aprueba el rol management"):
            approve_document(built, name, "Lucía Navarro Gil", role, DOCUMENT_APPROVED_AT)
    assert approval_keys(built, name) == []

    approve_document(built, name, "Marta Ibáñez Soler", Role.MANAGEMENT, DOCUMENT_APPROVED_AT)

    assert len(approval_history(built, name)) == 1


@pytest.mark.parametrize("name", DECLARED_AUTHORITY)
def test_procedimientos_y_autorizacion_solo_los_aprueba_el_rol_declarado(built, name):
    with pytest.raises(DocumentApprovalRejected, match="lo aprueba el rol security_officer"):
        approve_document(built, name, "Marta Ibáñez Soler", Role.MANAGEMENT, DOCUMENT_APPROVED_AT)
    assert approval_keys(built, name) == []

    approve_document(built, name, "Lucía Navarro Gil", Role.SECURITY_OFFICER, DOCUMENT_APPROVED_AT)

    assert approval_history(built, name)[0].role is Role.SECURITY_OFFICER


@pytest.mark.parametrize("name", DECLARED_AUTHORITY)
def test_la_autoridad_declarada_en_las_respuestas_cambia_quien_aprueba(built, name):
    answers = fixture_answers()
    answers["autoridad_procedimientos"] = "la dirección"
    write_answers(built, answers)
    build_documents(built)

    with pytest.raises(DocumentApprovalRejected, match="lo aprueba el rol management"):
        approve_document(
            built, name, "Lucía Navarro Gil", Role.SECURITY_OFFICER, DOCUMENT_APPROVED_AT
        )

    approve_document(built, name, "Marta Ibáñez Soler", Role.MANAGEMENT, DOCUMENT_APPROVED_AT)


def test_la_aprobacion_exige_el_documento_generado(answered):
    with pytest.raises(DocumentApprovalRejected, match="no está generado"):
        approve_document(
            answered,
            "politica-seguridad",
            "Marta Ibáñez Soler",
            Role.MANAGEMENT,
            DOCUMENT_APPROVED_AT,
        )


def test_la_aprobacion_de_un_documento_desconocido_falla(built):
    with pytest.raises(UnknownDocument):
        approve_document(
            built, "inventado", "Marta Ibáñez Soler", Role.MANAGEMENT, DOCUMENT_APPROVED_AT
        )


@pytest.mark.parametrize("name", DOCUMENTS)
def test_la_aprobacion_exige_alcance_aprobado(built, name):
    revoke_approval(built, "prueba", APPROVED_AT)

    with pytest.raises(ScopeNotApproved):
        approve_document(built, name, "Marta Ibáñez Soler", Role.MANAGEMENT, DOCUMENT_APPROVED_AT)

    assert approval_keys(built, name) == []


def test_docs_approve_por_cli_sin_alcance_aprobado_falla_sin_registrar(built, settings):
    revoke_approval(built, "prueba", APPROVED_AT)

    result = ens(
        settings,
        *(
            "docs",
            "approve",
            "politica-seguridad",
            "--by",
            "Marta Ibáñez Soler",
            "--role",
            "management",
        ),
    )

    assert result.exit_code == 1
    assert approval_keys(built, "politica-seguridad") == []


@pytest.mark.parametrize("relation", ["misma persona", "dependencia jerárquica"])
def test_la_aprobacion_queda_bloqueada_con_el_art_13_3_pendiente(built, relation):
    answers = fixture_answers()
    answers["relacion_seguridad_sistema"] = relation
    write_answers(built, answers)

    with pytest.raises(DocumentApprovalRejected) as raised:
        approve_document(
            built,
            "designacion-responsables",
            "Marta Ibáñez Soler",
            Role.MANAGEMENT,
            DOCUMENT_APPROVED_AT,
        )

    assert raised.value.errors[0] == (
        "justificación del art. 13.3 pendiente, la aprobación queda bloqueada"
    )
    assert approval_keys(built, "designacion-responsables") == []


def test_con_el_art_13_3_justificado_la_designacion_se_aprueba(built):
    answers = fixture_answers()
    answers.update(
        resp_sistema=answers["resp_seguridad"],
        relacion_seguridad_sistema="misma persona",
        causa_ausencia_recursos="Solo hay tres personas y una sola con formación técnica.",
        medidas_compensatorias=[
            {
                "medida": "Revisión trimestral de los accesos de administración",
                "verifica": "La gerente",
                "verificacion": "Acta firmada con la lista de cuentas revisadas",
            }
        ],
    )
    write_answers(built, answers)
    build_documents(built)

    approve_document(
        built,
        "designacion-responsables",
        "Marta Ibáñez Soler",
        Role.MANAGEMENT,
        DOCUMENT_APPROVED_AT,
    )

    assert len(approval_history(built, "designacion-responsables")) == 1


def test_no_se_aprueba_un_documento_editado_a_mano(built):
    path = "org/docs/politica-seguridad.md"
    built.write_text(path, built.read_text(path) + "\nMe lo he inventado.\n")

    with pytest.raises(DocumentApprovalRejected, match="editado a mano"):
        approve_document(
            built, "politica-seguridad", "Marta Ibáñez Soler", Role.MANAGEMENT, DOCUMENT_APPROVED_AT
        )


def test_no_se_aprueba_con_las_respuestas_cambiadas_sin_regenerar(built):
    answers = fixture_answers()
    answers["mision"] = "Otra misión distinta."
    write_answers(built, answers)

    with pytest.raises(DocumentApprovalRejected, match="respuestas cambiadas"):
        approve_document(
            built, "politica-seguridad", "Marta Ibáñez Soler", Role.MANAGEMENT, DOCUMENT_APPROVED_AT
        )


@pytest.mark.parametrize("by", ["", "   "])
def test_quien_aprueba_no_puede_estar_vacio(built, by):
    with pytest.raises(DocumentApprovalRejected):
        approve_document(built, "politica-seguridad", by, Role.MANAGEMENT, DOCUMENT_APPROVED_AT)

    assert approval_keys(built, "politica-seguridad") == []


@pytest.mark.parametrize("name", DECLARED_AUTHORITY)
def test_cambiar_el_firmante_de_la_direccion_no_invalida_lo_que_aprueba_otro_rol(built, name):
    approve_document(built, name, "Lucía Navarro Gil", Role.SECURITY_OFFICER, DOCUMENT_APPROVED_AT)
    answers = fixture_answers()
    answers["firmante_direccion"] = "Hugo Serrano Ortiz"
    write_answers(built, answers)
    build_documents(built)

    assert document_status(built, name).state is DocumentState.APPROVED


def test_la_aprobacion_registra_el_nombre_declarado_y_no_el_tecleado(built):
    approve_document(
        built, "politica-seguridad", "marta ibanez soler", Role.MANAGEMENT, DOCUMENT_APPROVED_AT
    )

    assert approval_history(built, "politica-seguridad")[0].approved_by == "Marta Ibáñez Soler"
