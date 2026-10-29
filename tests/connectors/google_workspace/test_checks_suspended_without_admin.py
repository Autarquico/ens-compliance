"""`workspace.suspended_without_admin_role` sobre datos mínimos."""

import pytest

from ens_evidence.connectors.google_workspace.checks import suspended_without_admin_role
from ens_evidence.models.enums import CheckStatus, CollectionStatus

from .check_data import (
    EVIDENCE_ID,
    OMITTED,
    admins_section,
    by_subject,
    only,
    result,
    roles_item,
    run,
    section,
    user,
)


def evaluate_item(who, item):
    evidence = result([who], admins=admins_section(*([item] if item else [])))
    return only(run(suspended_without_admin_role, evidence))


def gone(**flags):
    return user("baja@acme.es", suspended=True, **flags)


def test_suspendido_con_rol_de_administrador_da_fail_identificando_al_usuario():
    who = gone()

    outcome = evaluate_item(who, roles_item(who, ["Soporte"]))

    assert outcome.status is CheckStatus.FAIL
    assert outcome.subject == "baja@acme.es"
    assert outcome.check_id == "workspace.suspended_without_admin_role"


@pytest.mark.parametrize("flag", ["is_admin", "is_delegated_admin"])
def test_suspendido_con_flag_de_admin_da_fail(flag):
    who = gone(**{flag: True})

    assert evaluate_item(who, roles_item(who, [])).status is CheckStatus.FAIL


def test_suspendido_con_flag_de_admin_da_fail_aunque_la_consulta_de_roles_fallara():
    who = gone(is_admin=True)

    assert evaluate_item(who, roles_item(who, None, status="ERROR")).status is CheckStatus.FAIL


def test_suspendido_sin_roles_y_con_evidencia_negativa_completa_da_pass():
    who = gone()

    outcome = evaluate_item(who, roles_item(who, []))

    assert outcome.status is CheckStatus.PASS
    assert outcome.evidence_ids == [EVIDENCE_ID]


@pytest.mark.parametrize("status", ["PERMISSION_DENIED", "ERROR"])
def test_consulta_de_roles_no_completada_nunca_da_pass(status):
    who = gone()

    outcome = evaluate_item(who, roles_item(who, None, status=status, reason="x"))

    assert outcome.status is not CheckStatus.PASS
    assert outcome.subject == "baja@acme.es"


def test_consulta_de_roles_denegada_da_unknown_permission_denied():
    who = gone()

    outcome = evaluate_item(who, roles_item(who, None, status="PERMISSION_DENIED"))

    assert outcome.status is CheckStatus.UNKNOWN
    assert outcome.reason_code == "permission_denied"
    assert outcome.collection_status is CollectionStatus.PERMISSION_DENIED


def test_consulta_de_roles_truncada_da_error_traducido():
    who = gone()

    outcome = evaluate_item(who, roles_item(who, None, status="ERROR", reason="límite"))

    assert outcome.status is CheckStatus.ERROR


def test_suspendido_sin_consulta_de_roles_en_la_evidencia_da_unknown():
    assert evaluate_item(gone(), None).status is CheckStatus.UNKNOWN


def test_roles_con_consulta_exitosa_pero_nulos_dan_unknown():
    who = gone()

    assert evaluate_item(who, roles_item(who, None)).status is CheckStatus.UNKNOWN


@pytest.mark.parametrize("flag", ["is_admin", "is_delegated_admin"])
@pytest.mark.parametrize("value", [OMITTED, None])
def test_flag_de_admin_desconocido_impide_el_pass(flag, value):
    who = gone(**{flag: value})

    assert evaluate_item(who, roles_item(who, [])).status is CheckStatus.UNKNOWN


def test_usuario_activo_se_omite():
    evidence = result([user("ana@acme.es", is_admin=True)])

    assert run(suspended_without_admin_role, evidence) == []


@pytest.mark.parametrize("value", [OMITTED, None, "true", 1])
def test_suspension_desconocida_o_mal_formada_da_unknown_sin_omitirse(value):
    who = user("raro@acme.es", suspended=value)

    outcome = evaluate_item(who, roles_item(who, []))

    assert outcome.status is CheckStatus.UNKNOWN
    assert outcome.subject == "raro@acme.es"


def test_suspendido_y_archivado_se_evalua():
    who = gone(archived=True)

    assert evaluate_item(who, roles_item(who, ["Soporte"])).status is CheckStatus.FAIL


def test_cada_suspendido_recibe_su_resultado():
    a, b = user("a@acme.es", suspended=True), user("b@acme.es", suspended=True)
    evidence = result([a, b], admins=admins_section(roles_item(a, ["X"]), roles_item(b, [])))

    found = by_subject(run(suspended_without_admin_role, evidence))

    assert found["a@acme.es"].status is CheckStatus.FAIL
    assert found["b@acme.es"].status is CheckStatus.PASS


def test_seccion_de_admins_ausente_da_unknown_para_cada_suspendido():
    evidence = result([gone()], admins=None)

    outcome = only(run(suspended_without_admin_role, evidence))

    assert outcome.status is CheckStatus.UNKNOWN


@pytest.mark.parametrize("status", ["PERMISSION_DENIED", "ERROR"])
def test_seccion_de_usuarios_no_recogida_nunca_da_pass(status):
    outcome = only(
        run(suspended_without_admin_role, result(users_section=section(status=status, reason="x")))
    )

    assert outcome.status is not CheckStatus.PASS
    assert outcome.subject is None
