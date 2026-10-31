from datetime import timedelta

import pytest
import yaml

from ens_evidence.models.enums import Role
from ens_evidence.orgdocs import (
    BuildStatus,
    DocumentState,
    all_statuses,
    approval_history,
    approve_document,
    build_documents,
    document_status,
)
from ens_evidence.scope import revoke_approval

from .orgdocs_helpers import (
    APPROVED_AT,
    DOCUMENTS,
    SIGNATORY,
    ens,
    fixture_answers,
    write_answers,
)

DOCUMENT_APPROVED_AT = APPROVED_AT + timedelta(days=1)
LATER = APPROVED_AT + timedelta(days=30)
APPROVER = {
    "politica-seguridad": Role.MANAGEMENT,
    "normativa-seguridad": Role.MANAGEMENT,
    "designacion-responsables": Role.MANAGEMENT,
    "procedimientos-seguridad": Role.SECURITY_OFFICER,
    "proceso-autorizacion": Role.SECURITY_OFFICER,
    "procedimiento-incidentes": Role.SECURITY_OFFICER,
}


@pytest.fixture
def approved(built):
    for name in DOCUMENTS:
        approve_document(
            built, name, SIGNATORY[APPROVER[name]], APPROVER[name], DOCUMENT_APPROVED_AT
        )
    return built


def state(store, name):
    return document_status(store, name)


def change_answer(store, **changes):
    answers = fixture_answers()
    answers.update(changes)
    write_answers(store, answers)


def rewrite_approval(store, name, **changes):
    (key,) = store.list(f"org/docs/approvals/{name}/")
    record = yaml.safe_load(store.read_text(key))
    for field, value in changes.items():
        target = record
        *parents, last = field.split(".")
        for parent in parents:
            target = target[parent]
        target[last] = value
    store.write_text(key, yaml.safe_dump(record))


def test_sin_documentos_generados_el_estado_es_sin_generar(answered):
    assert {s.state for s in all_statuses(answered)} == {DocumentState.NOT_GENERATED}


def test_un_documento_generado_sin_aprobar_figura_como_generado(built):
    assert {s.state for s in all_statuses(built)} == {DocumentState.GENERATED}


def test_un_documento_aprobado_y_sin_cambios_es_vigente(approved):
    assert {s.state for s in all_statuses(approved)} == {DocumentState.APPROVED}


def test_un_build_identico_conserva_la_aprobacion(approved):
    report = build_documents(approved)

    assert {d.status for d in report.documents} == {BuildStatus.UNCHANGED}
    assert {s.state for s in all_statuses(approved)} == {DocumentState.APPROVED}


def test_docs_status_por_cli_sale_con_cero_si_todo_esta_vigente(approved, settings):
    result = ens(settings, "docs", "status")

    assert result.exit_code == 0, result.output
    assert result.output.count("aprobado y vigente") == len(DOCUMENTS)


def test_docs_status_por_cli_sale_con_uno_si_algo_no_esta_vigente(approved, settings):
    approved.write_text("org/docs/politica-seguridad.md", "# Otra cosa\n")

    result = ens(settings, "docs", "status")

    assert result.exit_code == 1
    assert "politica-seguridad: aprobado pero obsoleto" in result.output
    assert "documento editado a mano" in result.output


def test_un_documento_editado_a_mano_queda_obsoleto(approved):
    path = "org/docs/politica-seguridad.md"
    approved.write_text(path, approved.read_text(path) + "\nAñadido a mano.\n")

    status = state(approved, "politica-seguridad")

    assert status.state is DocumentState.OBSOLETE
    assert "documento editado a mano" in status.reasons
    assert state(approved, "normativa-seguridad").state is DocumentState.APPROVED


def test_respuestas_cambiadas_sin_regenerar_dejan_obsoleto_el_documento(approved):
    change_answer(approved, mision="Una misión nueva y distinta.")

    status = state(approved, "politica-seguridad")

    assert status.state is DocumentState.OBSOLETE
    assert status.reasons == ["respuestas cambiadas"]


def test_regenerar_con_respuestas_cambiadas_deja_obsoleto_el_documento_con_motivo(approved):
    change_answer(approved, mision="Una misión nueva y distinta.")
    build_documents(approved)

    status = state(approved, "politica-seguridad")

    assert status.state is DocumentState.OBSOLETE
    assert "documento regenerado con contenido distinto" in status.reasons
    assert "respuestas cambiadas" in status.reasons


def test_cambiar_una_respuesta_ajena_a_un_documento_no_lo_invalida(approved):
    change_answer(approved, mision="Una misión nueva y distinta.")
    build_documents(approved)

    assert state(approved, "normativa-seguridad").state is DocumentState.APPROVED
    assert state(approved, "procedimientos-seguridad").state is DocumentState.APPROVED


def test_respuestas_que_dejan_de_ser_validas_dejan_obsoleto_el_documento(approved):
    change_answer(approved, mision="")

    status = state(approved, "politica-seguridad")

    assert status.state is DocumentState.OBSOLETE
    assert "las respuestas actuales no son válidas o están pendientes" in status.reasons


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("template.version", 2, "plantilla cambiada de versión"),
        ("renderer_version", 2, "renderizador cambiado"),
        ("catalog_sha256", "b" * 64, "catálogo cambiado"),
        ("articulado_sha256", "c" * 64, "fuente del articulado cambiada"),
    ],
)
def test_plantilla_renderizador_catalogo_o_fuente_cambiados_dejan_obsoleto_el_documento(
    approved, field, value, reason
):
    rewrite_approval(approved, "politica-seguridad", **{field: value})

    status = state(approved, "politica-seguridad")

    assert status.state is DocumentState.OBSOLETE
    assert reason in status.reasons


def test_un_alcance_cambiado_deja_obsoletos_los_documentos(approved):
    text = approved.read_text("scope/assets.yaml")
    approved.write_text("scope/assets.yaml", text.replace("macOS 15", "macOS 16", 1))

    statuses = all_statuses(approved)

    assert {s.state for s in statuses} == {DocumentState.OBSOLETE}
    assert all("alcance cambiado desde la aprobación del documento" in s.reasons for s in statuses)


def test_un_alcance_revocado_deja_obsoletos_los_documentos(approved):
    revoke_approval(approved, "prueba", LATER)

    statuses = all_statuses(approved)

    assert {s.state for s in statuses} == {DocumentState.OBSOLETE}
    assert all("aprobación del alcance revocada o ausente" in s.reasons for s in statuses)


def test_un_documento_obsoleto_se_puede_aprobar_de_nuevo_y_el_historial_se_conserva(approved):
    change_answer(approved, mision="Una misión nueva y distinta.")
    build_documents(approved)

    approve_document(approved, "politica-seguridad", "Marta Ibáñez Soler", Role.MANAGEMENT, LATER)

    history = approval_history(approved, "politica-seguridad")
    assert [a.approved_at for a in history] == [DOCUMENT_APPROVED_AT, LATER]
    assert history[0].document_sha256 != history[1].document_sha256
    assert state(approved, "politica-seguridad").approval == history[1]
    assert state(approved, "politica-seguridad").state is DocumentState.APPROVED


def test_la_version_anterior_del_documento_queda_en_el_historial_con_su_meta(approved):
    previous = approved.read_text("org/docs/politica-seguridad.md")
    previous_meta = approved.read_text("org/docs/politica-seguridad.meta.yaml")
    change_answer(approved, mision="Una misión nueva y distinta.")

    build_documents(approved)

    archived = approved.list("org/docs/history/politica-seguridad/")
    assert len(archived) == 2
    assert {approved.read_text(k) for k in archived} == {previous, previous_meta}
    assert approved.read_text("org/docs/politica-seguridad.md") != previous


def test_una_edicion_manual_se_avisa_y_se_archiva_antes_de_regenerar(approved):
    path = "org/docs/politica-seguridad.md"
    edited = approved.read_text(path) + "\nAñadido a mano.\n"
    approved.write_text(path, edited)

    report = build_documents(approved)

    result = next(d for d in report.documents if d.name == "politica-seguridad")
    assert result.status is BuildStatus.UPDATED
    assert result.edited_by_hand
    assert edited in {approved.read_text(k) for k in approved.list("org/docs/history/")}
    assert "Añadido a mano" not in approved.read_text(path)


def test_regenerar_tras_una_edicion_manual_restituye_el_documento_aprobado(approved):
    path = "org/docs/politica-seguridad.md"
    approved.write_text(path, approved.read_text(path) + "\nAñadido a mano.\n")

    build_documents(approved)

    assert state(approved, "politica-seguridad").state is DocumentState.APPROVED
