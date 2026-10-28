import pytest

from ens_evidence.models.enums import CheckStatus

from .check_data import app, build, only, policy, remote, rule, run

PASS, FAIL, UNKNOWN = CheckStatus.PASS, CheckStatus.FAIL, CheckStatus.UNKNOWN
HOST = "app.z.example"


def evaluate(*policies, **app_options):
    result = build([remote(rule(HOST))], [app("a", HOST, policies=policies, **app_options)])
    return only(run(result))


@pytest.mark.parametrize(
    "policies",
    [
        pytest.param([policy("allow", ["email"])], id="email"),
        pytest.param(
            [policy("allow", ["email_domain", "github-organization"])], id="varios-de-identidad"
        ),
        pytest.param([policy("allow", ["okta"])], id="idp"),
        pytest.param(
            [policy("allow", ["everyone"], ["email_domain"])], id="everyone-con-require-identidad"
        ),
        pytest.param([policy("allow", ["ip"], ["email"])], id="ip-con-require-identidad"),
        pytest.param(
            [policy("allow", ["email"], ["auth_method"])], id="identidad-con-require-auth-method"
        ),
        pytest.param([policy("deny", ["ip"]), policy("allow", ["email"])], id="deny-y-allow"),
    ],
)
def test_allow_de_identidad_sin_bypass_da_pass_y_cita_la_evidencia(policies):
    result = evaluate(*policies)

    assert result.status is PASS
    assert result.evidence_ids == ["ev-1"]
    assert "política allow de identidad" in result.reason


@pytest.mark.parametrize(
    ("policies", "fragment"),
    [
        pytest.param([policy("bypass", ["everyone"])], "bypass", id="bypass"),
        pytest.param([policy("bypass", ["ip"])], "bypass", id="bypass-por-ip"),
        pytest.param([policy("allow", ["everyone"])], "everyone sin require", id="everyone"),
        pytest.param(
            [policy("allow", ["everyone"]), policy("allow", ["email"])],
            "everyone sin require",
            id="everyone-junto-a-identidad",
        ),
        pytest.param(
            [policy("allow", ["email"]), policy("bypass", ["ip"])], "bypass", id="mixta-con-bypass"
        ),
    ],
)
def test_politica_que_deja_pasar_sin_autenticar_da_fail_con_su_motivo(policies, fragment):
    result = evaluate(*policies)

    assert result.status is FAIL
    assert fragment in result.reason


@pytest.mark.parametrize(
    ("policies", "fragment"),
    [
        pytest.param(
            [policy("non_identity", ["service_token"])],
            "ninguna política allow",
            id="solo-non-identity",
        ),
        pytest.param([policy("allow", ["ip"])], "no exigen identidad", id="solo-ip"),
        pytest.param([policy("allow", ["ip", "geo"])], "no exigen identidad", id="ip-y-geo"),
        pytest.param(
            [policy("allow", ["service_token"])], "no exigen identidad", id="service-token"
        ),
        pytest.param(
            [policy("allow", ["any_valid_service_token"])],
            "no exigen identidad",
            id="cualquier-service-token",
        ),
        pytest.param(
            [policy("allow", ["email", "unknown"])],
            "selectores de política desconocidos",
            id="selector-desconocido",
        ),
        pytest.param(
            [policy("allow", ["email"], ["invented"])],
            "selectores de política desconocidos",
            id="require-desconocido",
        ),
        pytest.param(
            [policy("allow", ["email"]), policy("magic", ["email"])],
            "decisión desconocida",
            id="decision-desconocida",
        ),
        pytest.param([], "ninguna política allow", id="sin-politicas"),
    ],
)
def test_politica_que_no_demuestra_identidad_da_unknown_con_motivo(policies, fragment):
    result = evaluate(*policies)

    assert result.status is UNKNOWN
    assert fragment in result.reason
    assert result.reason_code is None


def test_app_sin_politicas_recogidas_con_seccion_correcta_da_unknown():
    result = evaluate(collected_policies=False)

    assert result.status is UNKNOWN
    assert "no consta ninguna política" in result.reason


def test_la_politica_permisiva_gana_aunque_otra_no_sea_demostrable():
    result = evaluate(policy("allow", ["unknown"]), policy("bypass", ["everyone"]))

    assert result.status is FAIL


@pytest.mark.parametrize("selector", ["login_method", "auth_method", "external_evaluation"])
def test_selector_que_no_identifica_solo_en_include_da_unknown(selector):
    result = evaluate(policy("allow", [selector]))

    assert result.status is UNKNOWN


def test_everyone_con_require_que_no_identifica_no_da_pass():
    result = evaluate(policy("allow", ["everyone"], ["login_method"]))

    assert result.status is not PASS
