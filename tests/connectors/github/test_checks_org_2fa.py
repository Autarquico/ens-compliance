"""`github.org_2fa_required` sobre la recogida real y sobre datos mínimos."""

import pytest

from ens_evidence.connectors.github.checks import org_2fa_required
from ens_evidence.models.enums import CheckStatus, CollectionStatus

from .check_data import EVIDENCE_ID, only, result, run, section, two_factor
from .fake_api import ORG, fixture


def check(evidence):
    return only(run(org_2fa_required, evidence))


def test_organizacion_que_exige_2fa_da_pass_citando_la_evidencia(collect):
    outcome = check(collect())

    assert outcome.status is CheckStatus.PASS
    assert outcome.evidence_ids == [EVIDENCE_ID]
    assert outcome.subject == ORG
    assert outcome.check_id == "github.org_2fa_required"
    assert outcome.asset_id == "gh-taller"


def test_organizacion_que_no_exige_2fa_da_fail(api, collect):
    api.set(rf"/orgs/{ORG}", {**fixture("org"), "two_factor_requirement_enabled": False})

    outcome = check(collect())

    assert outcome.status is CheckStatus.FAIL
    assert outcome.subject == ORG


def test_campo_de_2fa_no_visible_da_unknown_permission_denied(api, collect):
    org = fixture("org")
    del org["two_factor_requirement_enabled"]
    api.set(rf"/orgs/{ORG}", org)

    outcome = check(collect())

    assert outcome.status is CheckStatus.UNKNOWN
    assert outcome.reason_code == "permission_denied"
    assert outcome.collection_status is CollectionStatus.PERMISSION_DENIED


def test_campo_de_2fa_null_nunca_es_pass_ni_fail(api, collect):
    api.set(rf"/orgs/{ORG}", {**fixture("org"), "two_factor_requirement_enabled": None})

    outcome = check(collect())

    assert outcome.status is CheckStatus.UNKNOWN
    assert outcome.reason_code == "permission_denied"


@pytest.mark.parametrize("value", ["false", "true", 0, 1, "yes"])
def test_campo_de_2fa_que_no_es_booleano_no_da_pass_ni_fail(api, collect, value):
    api.set(rf"/orgs/{ORG}", {**fixture("org"), "two_factor_requirement_enabled": value})

    outcome = check(collect())

    assert outcome.status not in {CheckStatus.PASS, CheckStatus.FAIL}


def test_cero_miembros_sin_2fa_no_demuestra_el_requisito(api, collect):
    org = fixture("org")
    del org["two_factor_requirement_enabled"]
    api.set(rf"/orgs/{ORG}", org)
    api.set(rf"/orgs/{ORG}/members", [])

    assert check(collect()).status is CheckStatus.UNKNOWN


def test_recogida_de_la_organizacion_denegada_da_unknown_permission_denied(api, collect):
    api.set(rf"/orgs/{ORG}", (403, {"message": "Resource not accessible"}))

    outcome = check(collect())

    assert outcome.status is CheckStatus.UNKNOWN
    assert outcome.reason_code == "permission_denied"


def test_recogida_de_la_organizacion_con_error_da_error(api, collect):
    api.set(rf"/orgs/{ORG}", (500, {"message": "boom"}))

    outcome = check(collect())

    assert outcome.status is CheckStatus.ERROR


def test_seccion_organizacion_ausente_da_error_explicito():
    outcome = check(result())

    assert outcome.status is CheckStatus.ERROR
    assert "organization" in outcome.reason


def test_requisito_sin_campo_enabled_da_unknown():
    evidence = result(organization=section(two_factor_requirement={"status": "SUCCESS"}))

    assert check(evidence).status is CheckStatus.UNKNOWN


def test_requisito_booleano_da_pass_o_fail_segun_su_valor():
    assert check(result(organization=two_factor(True))).status is CheckStatus.PASS
    assert check(result(organization=two_factor(False))).status is CheckStatus.FAIL
