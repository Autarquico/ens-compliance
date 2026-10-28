"""`github.secret_scanning_enabled` sobre la recogida real."""

import pytest

from ens_evidence.connectors.github.checks import secret_scanning_enabled
from ens_evidence.models.enums import CheckStatus

from .check_data import EVIDENCE_ID, by_subject, run
from .fake_api import ORG, repo_body

REPOS = rf"/orgs/{ORG}/repos"


def checks(collect):
    return by_subject(run(secret_scanning_enabled, collect()))


def facturas_con(api, **analysis):
    api.set(REPOS, [repo_body("facturas", security_and_analysis=analysis), repo_body("infra")])


def test_secret_scanning_enabled_da_pass_citando_la_evidencia(collect):
    outcome = checks(collect)["facturas"]

    assert outcome.status is CheckStatus.PASS
    assert outcome.evidence_ids == [EVIDENCE_ID]
    assert outcome.check_id == "github.secret_scanning_enabled"
    assert outcome.asset_id == "gh-taller"


def test_secret_scanning_disabled_da_fail(collect):
    outcome = checks(collect)["infra"]

    assert outcome.status is CheckStatus.FAIL


def test_un_resultado_por_repositorio_con_subject_estable(collect):
    assert set(checks(collect)) == {"facturas", "infra"}
    assert set(checks(collect)) == set(checks(collect))


def test_security_and_analysis_no_visible_da_unknown_permission_denied(api, collect):
    api.set(REPOS, [repo_body("facturas", security_and_analysis=None), repo_body("infra")])

    outcome = checks(collect)["facturas"]

    assert outcome.status is CheckStatus.UNKNOWN
    assert outcome.reason_code == "permission_denied"


def test_la_ausencia_de_un_repo_no_condiciona_a_los_demas(api, collect):
    api.set(REPOS, [repo_body("facturas", security_and_analysis=None), repo_body("infra")])

    assert checks(collect)["infra"].status is CheckStatus.FAIL


def test_secret_scanning_no_disponible_en_el_plan_da_unknown_not_supported(api, collect):
    facturas_con(api, advanced_security={"status": "disabled"})

    outcome = checks(collect)["facturas"]

    assert outcome.status is CheckStatus.UNKNOWN
    assert outcome.reason_code == "not_supported"


@pytest.mark.parametrize(
    "analysis",
    [
        {"secret_scanning_push_protection": {"status": "enabled"}},
        {"advanced_security": {"status": "enabled"}},
        {
            "advanced_security": {"status": "enabled"},
            "secret_scanning_push_protection": {"status": "enabled"},
        },
    ],
    ids=["solo-push-protection", "solo-advanced-security", "ambas-sin-secret-scanning"],
)
def test_push_protection_o_advanced_security_sin_secret_scanning_no_dan_pass(
    api, collect, analysis
):
    facturas_con(api, **analysis)

    outcome = checks(collect)["facturas"]

    assert outcome.status is CheckStatus.UNKNOWN
    assert outcome.reason_code == "not_supported"


def test_secret_scanning_disabled_con_push_protection_enabled_sigue_siendo_fail(api, collect):
    facturas_con(
        api,
        secret_scanning={"status": "disabled"},
        secret_scanning_push_protection={"status": "enabled"},
    )

    assert checks(collect)["facturas"].status is CheckStatus.FAIL


@pytest.mark.parametrize("status", [None, "Enabled", "ENABLED", "on", True, 1, ""])
def test_estado_de_secret_scanning_inesperado_no_da_pass(api, collect, status):
    facturas_con(api, secret_scanning={"status": status})

    results = run(secret_scanning_enabled, collect())

    assert all(r.status is not CheckStatus.PASS for r in results if r.subject != "infra")


def test_secret_scanning_null_no_da_pass(api, collect):
    facturas_con(api, secret_scanning=None)

    assert checks(collect)["facturas"].status is not CheckStatus.PASS


def test_repositorios_no_recogidos_dan_un_unico_resultado_sin_pass(api, collect):
    api.set(REPOS, (403, {"message": "Resource not accessible"}))

    results = run(secret_scanning_enabled, collect())

    assert [r.status for r in results] == [CheckStatus.UNKNOWN]
    assert results[0].reason_code == "permission_denied"
