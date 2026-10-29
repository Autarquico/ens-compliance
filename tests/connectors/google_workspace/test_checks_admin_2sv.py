"""`workspace.admin_2sv_enforced` sobre datos mínimos."""

import pytest

from ens_evidence.connectors.google_workspace.checks import admin_2sv_enforced
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


def evaluate(*users, roles=None, **kwargs):
    """`roles`: correo -> roles; el resto de usuarios tiene consulta completa sin roles."""
    roles = roles or {}
    admins = admins_section(*(roles_item(u, roles.get(u["email"], [])) for u in users))
    return run(admin_2sv_enforced, result(users, admins=kwargs.pop("admins", admins), **kwargs))


def test_super_admin_con_2sv_exigido_da_pass_citando_la_evidencia():
    outcome = only(evaluate(user("root@acme.es", is_admin=True, enforced=True)))

    assert outcome.status is CheckStatus.PASS
    assert outcome.evidence_ids == [EVIDENCE_ID]
    assert outcome.subject == "root@acme.es"
    assert outcome.check_id == "workspace.admin_2sv_enforced"


def test_super_admin_sin_2sv_exigido_da_fail():
    outcome = only(evaluate(user("root@acme.es", is_admin=True, enforced=False)))

    assert outcome.status is CheckStatus.FAIL
    assert outcome.subject == "root@acme.es"


@pytest.mark.parametrize("value", [OMITTED, None, "true", "false", 1, 0])
def test_flag_de_exigencia_ausente_o_mal_formado_da_unknown(value):
    outcome = only(evaluate(user(is_admin=True, enforced=value)))

    assert outcome.status is CheckStatus.UNKNOWN


def test_admin_delegado_por_flag_se_evalua():
    outcome = only(evaluate(user("help@acme.es", is_delegated_admin=True, enforced=False)))

    assert outcome.status is CheckStatus.FAIL
    assert outcome.subject == "help@acme.es"


def test_admin_solo_por_asignacion_directa_de_rol_se_evalua():
    outcome = only(
        evaluate(user("rol@acme.es", enforced=False), roles={"rol@acme.es": ["Soporte"]})
    )

    assert outcome.status is CheckStatus.FAIL
    assert outcome.subject == "rol@acme.es"


def test_admin_solo_por_rol_heredado_por_grupo_con_2sv_da_pass():
    # Las asignaciones indirectas llegan en `roles` igual que las directas.
    outcome = only(
        evaluate(user("grupo@acme.es", enforced=True), roles={"grupo@acme.es": ["Via grupo"]})
    )

    assert outcome.status is CheckStatus.PASS


def test_flags_contradictorios_con_roles_cuentan_como_admin():
    outcome = only(
        evaluate(
            user("raro@acme.es", is_admin=False, is_delegated_admin=False, enforced=False),
            roles={"raro@acme.es": ["Soporte"]},
        )
    )

    assert outcome.status is CheckStatus.FAIL


def test_usuario_normal_con_evidencia_negativa_completa_no_genera_resultado():
    assert evaluate(user("ana@acme.es", enforced=False)) == []


def test_solo_se_evaluan_los_admins_entre_varios_usuarios():
    found = by_subject(
        evaluate(
            user("ana@acme.es"),
            user("root@acme.es", is_admin=True, enforced=True),
            user("rol@acme.es", enforced=False),
            roles={"rol@acme.es": ["Soporte"]},
        )
    )

    assert set(found) == {"root@acme.es", "rol@acme.es"}


@pytest.mark.parametrize("state", [{"suspended": True}, {"archived": True}])
def test_admin_suspendido_o_archivado_se_evalua_igualmente(state):
    outcome = only(evaluate(user("root@acme.es", is_admin=True, enforced=False, **state)))

    assert outcome.status is CheckStatus.FAIL
    assert outcome.subject == "root@acme.es"


def test_descubrimiento_marcado_incompleto_anade_unknown_sin_sujeto():
    people = [user("root@acme.es", is_admin=True, enforced=True), user("ana@acme.es")]
    admins = admins_section(*(roles_item(p) for p in people), complete=False)

    found = by_subject(evaluate(*people, admins=admins))

    assert found["root@acme.es"].status is CheckStatus.PASS
    assert found[None].status is CheckStatus.UNKNOWN
    assert "incompleto" in found[None].reason


def test_descubrimiento_incompleto_sin_ningun_admin_conocido_nunca_da_pass():
    admins = admins_section(roles_item(user()), complete=False)

    outcome = only(evaluate(user(), admins=admins))

    assert outcome.status is CheckStatus.UNKNOWN
    assert outcome.subject is None


@pytest.mark.parametrize("flag", ["is_admin", "is_delegated_admin"])
@pytest.mark.parametrize("value", [OMITTED, None])
def test_usuario_con_flag_de_admin_desconocido_impide_afirmar_que_no_es_admin(flag, value):
    outcome = only(evaluate(user("ana@acme.es", **{flag: value})))

    assert outcome.status is CheckStatus.UNKNOWN
    assert outcome.subject is None


def test_consulta_de_roles_truncada_de_un_usuario_deja_descubrimiento_en_unknown():
    people = [user("ana@acme.es"), user("root@acme.es", is_admin=True, enforced=True)]
    admins = admins_section(
        roles_item(people[0], None, status="ERROR", reason="límite"),
        roles_item(people[1]),
        status="ERROR",
    )

    found = by_subject(evaluate(*people, admins=admins))

    assert found["root@acme.es"].status is CheckStatus.PASS
    assert found[None].status is CheckStatus.ERROR


def test_consulta_de_roles_denegada_da_unknown_permission_denied():
    people = [user("ana@acme.es")]
    admins = admins_section(
        roles_item(people[0], None, status="PERMISSION_DENIED", reason="sin acceso"),
        status="PERMISSION_DENIED",
        reason="sin acceso",
    )

    outcome = only(evaluate(*people, admins=admins))

    assert outcome.status is CheckStatus.UNKNOWN
    assert outcome.reason_code == "permission_denied"
    assert outcome.collection_status is CollectionStatus.PERMISSION_DENIED


def test_seccion_de_admins_ausente_nunca_da_pass_y_conserva_los_admins_por_flag():
    people = [user("root@acme.es", is_admin=True, enforced=True)]

    outcomes = by_subject(run(admin_2sv_enforced, result(people, admins=None)))

    assert outcomes[None].status is CheckStatus.ERROR
    assert outcomes["root@acme.es"].status is CheckStatus.PASS


def test_roles_con_consulta_no_exitosa_no_cuentan_como_admin():
    people = [user("ana@acme.es", enforced=False)]
    admins = admins_section(
        roles_item(people[0], ["Soporte"], status="ERROR"), status="ERROR", reason="x"
    )

    outcomes = evaluate(*people, admins=admins)

    assert all(o.subject is None for o in outcomes)
    assert all(o.status is not CheckStatus.PASS for o in outcomes)


@pytest.mark.parametrize(
    ("status", "code", "check"),
    [
        ("PERMISSION_DENIED", "permission_denied", CheckStatus.UNKNOWN),
        ("ERROR", None, CheckStatus.ERROR),
    ],
)
def test_seccion_de_usuarios_no_recogida_nunca_da_pass(status, code, check):
    outcome = only(evaluate(users_section=section(status=status, reason="x")))

    assert outcome.status is check
    assert outcome.reason_code == code
    assert outcome.subject is None


def test_subject_estable_entre_evaluaciones():
    people = [user("root@acme.es", is_admin=True), user("rol@acme.es")]
    roles = {"rol@acme.es": ["Soporte"]}

    first = [r.subject for r in evaluate(*people, roles=roles)]
    second = [r.subject for r in evaluate(*people, roles=roles)]

    assert first == second == ["root@acme.es", "rol@acme.es"]
