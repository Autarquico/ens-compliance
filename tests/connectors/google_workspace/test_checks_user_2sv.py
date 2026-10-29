"""`workspace.user_2sv_enrolled` sobre datos mínimos."""

import pytest

from ens_evidence.connectors.google_workspace.checks import user_2sv_enrolled
from ens_evidence.models.enums import CheckStatus, CollectionStatus

from .check_data import EVIDENCE_ID, OMITTED, by_subject, only, result, run, section, user


def evaluate(*users, **kwargs):
    return run(user_2sv_enrolled, result(users, **kwargs))


def test_usuario_activo_sin_2sv_da_fail_identificando_al_usuario():
    outcome = only(evaluate(user("ana@acme.es", enrolled=False)))

    assert outcome.status is CheckStatus.FAIL
    assert outcome.subject == "ana@acme.es"
    assert outcome.check_id == "workspace.user_2sv_enrolled"
    assert outcome.asset_id == "gw-acme"


def test_usuario_con_2sv_da_pass_citando_la_evidencia():
    outcome = only(evaluate(user(enrolled=True)))

    assert outcome.status is CheckStatus.PASS
    assert outcome.evidence_ids == [EVIDENCE_ID]
    assert outcome.subject == "ana@acme.es"


def test_cada_usuario_recibe_su_propio_resultado():
    found = by_subject(
        evaluate(user("ana@acme.es", enrolled=True), user("luis@acme.es", enrolled=False))
    )

    assert found["ana@acme.es"].status is CheckStatus.PASS
    assert found["luis@acme.es"].status is CheckStatus.FAIL


def test_flag_ausente_da_unknown_nunca_fail():
    outcome = only(evaluate(user(enrolled=OMITTED)))

    assert outcome.status is CheckStatus.UNKNOWN


def test_flag_null_da_unknown():
    assert only(evaluate(user(enrolled=None))).status is CheckStatus.UNKNOWN


@pytest.mark.parametrize("value", ["true", "false", 0, 1, "yes", [], {}])
def test_flag_que_no_es_booleano_da_unknown(value):
    assert only(evaluate(user(enrolled=value))).status is CheckStatus.UNKNOWN


@pytest.mark.parametrize("missing", ["suspended", "archived"])
@pytest.mark.parametrize("value", [OMITTED, None, "false", 0])
def test_estado_activo_desconocido_da_unknown_aunque_tenga_2sv(missing, value):
    outcome = only(evaluate(user(enrolled=True, **{missing: value})))

    assert outcome.status is CheckStatus.UNKNOWN
    assert outcome.subject == "ana@acme.es"


def test_usuario_archivado_se_excluye():
    assert evaluate(user(archived=True, enrolled=False)) == []


def test_usuario_suspendido_se_excluye():
    assert evaluate(user(suspended=True, enrolled=False)) == []


def test_usuario_suspendido_y_archivado_se_excluye():
    assert evaluate(user(suspended=True, archived=True, enrolled=False)) == []


def test_archivado_confirmado_se_excluye_aunque_suspendido_sea_desconocido():
    assert evaluate(user(archived=True, suspended=OMITTED)) == []


def test_suspendido_confirmado_se_excluye_aunque_archivado_sea_desconocido():
    assert evaluate(user(suspended=True, archived=None)) == []


def test_exclusion_de_un_usuario_no_afecta_a_los_demas():
    found = by_subject(
        evaluate(user("baja@acme.es", suspended=True), user("ana@acme.es", enrolled=False))
    )

    assert set(found) == {"ana@acme.es"}


def test_usuario_sin_correo_se_identifica_por_id():
    who = {**user(enrolled=False), "email": None}

    assert only(evaluate(who)).subject == who["id"]


def test_subject_estable_entre_evaluaciones():
    users = [user("ana@acme.es"), user("luis@acme.es", enrolled=False)]

    first = [r.subject for r in evaluate(*users)]
    second = [r.subject for r in evaluate(*users)]

    assert first == second == ["ana@acme.es", "luis@acme.es"]


def test_sin_usuarios_no_hay_resultados():
    assert evaluate() == []


@pytest.mark.parametrize(
    ("status", "code", "check"),
    [
        ("PERMISSION_DENIED", "permission_denied", CheckStatus.UNKNOWN),
        ("NOT_SUPPORTED", "not_supported", CheckStatus.UNKNOWN),
        ("ERROR", None, CheckStatus.ERROR),
    ],
)
def test_seccion_de_usuarios_no_recogida_nunca_da_pass(status, code, check):
    outcome = only(evaluate(users_section=section(status=status, reason="x")))

    assert outcome.status is check
    assert outcome.reason_code == code
    assert outcome.collection_status is CollectionStatus(status)
    assert outcome.subject is None


def test_seccion_de_usuarios_ausente_da_error():
    evidence = result()
    del evidence.data["sections"]["users"]

    outcome = only(run(user_2sv_enrolled, evidence))

    assert outcome.status is CheckStatus.ERROR


def test_estado_de_seccion_desconocido_se_trata_como_error():
    outcome = only(evaluate(users_section=section([user()], status="???")))

    assert outcome.status is CheckStatus.ERROR
