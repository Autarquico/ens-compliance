"""Scenarios de la spec que cruzan los tres checks."""

import pytest

from ens_evidence.connectors.google_workspace.checks import (
    admin_2sv_enforced,
    suspended_without_admin_role,
    user_2sv_enrolled,
)
from ens_evidence.models.enums import CheckStatus

from .check_data import admins_section, result, roles_item, run, section, user

CHECKS = [user_2sv_enrolled, admin_2sv_enforced, suspended_without_admin_role]


def everything_ok():
    admin = user("root@acme.es", is_admin=True)
    gone = user("baja@acme.es", suspended=True)
    people = [user(), admin, gone]
    return people, admins_section(*(roles_item(p, ["super"] if p is admin else []) for p in people))


@pytest.mark.parametrize("check", CHECKS)
@pytest.mark.parametrize("collection_status", ["ERROR", "PERMISSION_DENIED", "NOT_SUPPORTED"])
def test_credencial_revocada_no_da_pass_en_ningun_check(check, collection_status):
    people, admins = everything_ok()
    evidence = result(
        people, admins=admins, collection_status=collection_status, error="credencial revocada"
    )

    outcomes = run(check, evidence)

    assert outcomes
    assert all(o.status is not CheckStatus.PASS for o in outcomes)


def test_la_misma_evidencia_con_recogida_correcta_si_produce_pass():
    people, admins = everything_ok()

    outcomes = [o for check in CHECKS for o in run(check, result(people, admins=admins))]

    assert any(o.status is CheckStatus.PASS for o in outcomes)


@pytest.mark.parametrize("check", CHECKS)
def test_permiso_insuficiente_da_unknown_permission_denied_nunca_pass(check):
    people, admins = everything_ok()
    denied = section(status="PERMISSION_DENIED", reason="sin acceso")

    outcomes = run(check, result(people, admins=admins, users_section=denied))

    assert [o.status for o in outcomes] == [CheckStatus.UNKNOWN]
    assert outcomes[0].reason_code == "permission_denied"
