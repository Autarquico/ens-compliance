"""`github.dependabot_alerts_enabled` sobre la recogida real."""

import pytest

from ens_evidence.connectors.github.checks import dependabot_alerts_enabled
from ens_evidence.models.enums import CheckStatus

from .check_data import EVIDENCE_ID, by_subject, run
from .fake_api import FACTURAS, ORG, denied, message, not_found

DISABLED = (403, message("Dependabot alerts are disabled for this repository."))


def checks(collect):
    return by_subject(run(dependabot_alerts_enabled, collect()))


def test_204_de_vulnerability_alerts_da_pass_citando_la_evidencia(collect):
    outcome = checks(collect)["facturas"]

    assert outcome.status is CheckStatus.PASS
    assert outcome.evidence_ids == [EVIDENCE_ID]
    assert outcome.check_id == "github.dependabot_alerts_enabled"
    assert outcome.asset_id == "gh-taller"


def test_un_resultado_por_repositorio_con_subject_estable(collect):
    assert set(checks(collect)) == {"facturas", "infra"}


def test_mensaje_explicito_de_alertas_desactivadas_da_fail(api, collect):
    api.set(rf"{FACTURAS}/vulnerability-alerts", not_found())
    api.set(rf"{FACTURAS}/dependabot/alerts", DISABLED)

    results = checks(collect)

    assert results["facturas"].status is CheckStatus.FAIL
    assert results["infra"].status is CheckStatus.PASS


def test_404_generico_en_ambos_endpoints_da_unknown_permission_denied(api, collect):
    api.set(rf"{FACTURAS}/vulnerability-alerts", not_found())
    api.set(rf"{FACTURAS}/dependabot/alerts", not_found())

    outcome = checks(collect)["facturas"]

    assert outcome.status is CheckStatus.UNKNOWN
    assert outcome.reason_code == "permission_denied"


def test_sin_permiso_en_vulnerability_alerts_da_unknown_permission_denied(api, collect):
    api.set(rf"{FACTURAS}/vulnerability-alerts", denied())

    outcome = checks(collect)["facturas"]

    assert outcome.status is CheckStatus.UNKNOWN
    assert outcome.reason_code == "permission_denied"


def test_404_con_alertas_denegadas_genericamente_no_da_fail(api, collect):
    api.set(rf"{FACTURAS}/vulnerability-alerts", not_found())
    api.set(rf"{FACTURAS}/dependabot/alerts", denied())

    assert checks(collect)["facturas"].status is CheckStatus.UNKNOWN


@pytest.mark.parametrize("status", [200, 202])
def test_respuesta_distinta_de_204_no_da_pass(api, collect, status):
    api.set(rf"{FACTURAS}/vulnerability-alerts", (status, {}))

    assert checks(collect)["facturas"].status is not CheckStatus.PASS


def test_alertas_activadas_con_alertas_abiertas_siguen_siendo_pass(api, collect):
    api.set(rf"{FACTURAS}/dependabot/alerts", [{"number": 1}])

    assert checks(collect)["facturas"].status is CheckStatus.PASS


def test_un_repo_denegado_no_condiciona_a_los_demas(api, collect):
    api.set(rf"{FACTURAS}/vulnerability-alerts", denied())

    assert checks(collect)["infra"].status is CheckStatus.PASS


def test_rate_limit_en_vulnerability_alerts_da_error_no_fail(api, collect):
    api.set(
        rf"{FACTURAS}/vulnerability-alerts",
        (403, message("API rate limit exceeded"), {"x-ratelimit-remaining": "0"}),
    )

    outcome = checks(collect)["facturas"]

    assert outcome.status is CheckStatus.ERROR


def test_repositorios_no_recogidos_dan_un_unico_resultado_sin_pass(api, collect):
    api.set(rf"/orgs/{ORG}/repos", denied())

    results = run(dependabot_alerts_enabled, collect())

    assert [r.status for r in results] == [CheckStatus.UNKNOWN]
