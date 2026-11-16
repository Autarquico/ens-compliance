"""Tabla de verdad de `cloudflare.ui_access_requires_mfa`.

La forma de la MFA en la API de Cloudflare no está verificada contra una cuenta real: se usan
fixtures con la forma documentada (`require` `auth_method` mfa, `mfa_config`).
"""

import pytest

from ens_evidence.checks.registry import CHECK_FUNCTIONS
from ens_evidence.connectors.cloudflare.checks import UI_ACCESS_REQUIRES_MFA
from ens_evidence.connectors.cloudflare.mfa_checks import ui_access_requires_mfa
from ens_evidence.models.contracts import CheckResult
from ens_evidence.models.enums import CheckStatus

from .check_data import EVIDENCE_ID, build, ipolicy, section, sel
from .check_data import app as plain_app
from .fake_api import ACCOUNT, NOW, envelope

PASS, FAIL, UNKNOWN, ERROR = (
    CheckStatus.PASS,
    CheckStatus.FAIL,
    CheckStatus.UNKNOWN,
    CheckStatus.ERROR,
)
UI = "ui.example.com"


def mfa_policy(
    decision="allow", name="p", mfa_required=False, methods=("mfa",), include=("email",)
):
    selectors = [sel("auth_method") for _ in methods]
    policy = ipolicy(decision, [sel(kind) for kind in include], selectors)
    return {
        **policy,
        "id": name,
        "name": name,
        "mfa_required": mfa_required,
        "require_auth_methods": list(methods),
    }


def ui_app(*policies, app_id="ui", destination=UI, mfa_required=False, **kwargs):
    entry = plain_app(app_id, destination, policies=policies, **kwargs)
    entry["app"]["mfa_required"] = mfa_required
    return entry


def evaluate(*apps, hostname=UI, groups=(), **kwargs) -> CheckResult:
    result = build([], apps, groups_section=section(groups), **kwargs)
    result.data["ui_hostname"] = hostname
    (check,) = ui_access_requires_mfa(result, EVIDENCE_ID, NOW)
    CheckResult.model_validate(check.model_dump())
    return check


def test_toda_allow_con_require_mfa_es_pass():
    result = evaluate(ui_app(mfa_policy(name="a"), mfa_policy(name="b")))

    assert (result.status, result.reason_code) == (PASS, None)
    assert result.evidence_ids == [EVIDENCE_ID]
    assert "no prueba" in result.reason


def test_mfa_independiente_de_la_politica_o_de_la_aplicacion_es_pass():
    by_policy = evaluate(ui_app(mfa_policy(methods=(), mfa_required=True)))
    by_app = evaluate(ui_app(mfa_policy(methods=()), mfa_required=True))

    assert by_policy.status is PASS
    assert by_app.status is PASS


def test_allow_sin_mfa_es_fail_y_nombra_la_politica():
    result = evaluate(ui_app(mfa_policy(name="equipo"), mfa_policy(name="invitados", methods=())))

    assert (result.status, result.reason_code) == (FAIL, "mfa_missing")
    assert "«invitados»" in result.reason
    assert "«equipo»" not in result.reason


def test_auth_method_distinto_de_mfa_no_cuenta():
    result = evaluate(ui_app(mfa_policy(name="pwd", methods=("pwd",))))

    assert (result.status, result.reason_code) == (FAIL, "mfa_missing")


def test_auth_method_ilegible_no_cuenta():
    result = evaluate(ui_app(mfa_policy(name="raro", methods=("unreadable",))))

    assert result.status is FAIL


def test_everyone_sin_require_es_fail():
    policy = {**mfa_policy(name="todos", methods=(), include=("everyone",))}

    assert evaluate(ui_app(policy)).status is FAIL


def test_bypass_es_fail_aunque_las_demas_exijan_mfa():
    result = evaluate(ui_app(mfa_policy(name="ok"), mfa_policy("bypass", name="abierta")))

    assert (result.status, result.reason_code) == (FAIL, "bypass")
    assert "«abierta»" in result.reason


def test_deny_no_exige_mfa():
    result = evaluate(ui_app(mfa_policy(name="ok"), mfa_policy("deny", name="bloqueo", methods=())))

    assert result.status is PASS


def test_token_de_servicio_es_unknown():
    result = evaluate(ui_app(mfa_policy(name="ok"), mfa_policy("non_identity", name="maquinas")))

    assert (result.status, result.reason_code) == (UNKNOWN, "non_identity")


def test_decision_desconocida_es_unknown():
    result = evaluate(ui_app(mfa_policy("rara", name="x")))

    assert (result.status, result.reason_code) == (UNKNOWN, "policy_unresolved")


def test_sin_politicas_allow_es_unknown():
    result = evaluate(ui_app(mfa_policy("deny", name="solo-deny")))

    assert (result.status, result.reason_code) == (UNKNOWN, "no_allow_policy")


def test_sin_ui_hostname_es_unknown_con_motivo():
    result = evaluate(ui_app(mfa_policy()), hostname=None)

    assert (result.status, result.reason_code) == (UNKNOWN, "ui_hostname_not_configured")
    assert "ui_hostname" in result.reason


def test_sin_aplicacion_para_el_hostname_es_unknown():
    result = evaluate(ui_app(mfa_policy(), destination="otra.example.com"))

    assert (result.status, result.reason_code) == (UNKNOWN, "ui_app_not_found")


def test_aplicacion_que_cubre_solo_una_ruta_es_unknown():
    result = evaluate(ui_app(mfa_policy(), destination=f"{UI}/admin"))

    assert (result.status, result.reason_code) == (UNKNOWN, "ui_app_partial")


def test_comodin_que_cubre_el_hostname_se_evalua():
    result = evaluate(ui_app(mfa_policy(name="s", methods=()), destination="*.example.com"))

    assert (result.status, result.reason_code) == (FAIL, "mfa_missing")


def test_todas_las_aplicaciones_que_cubren_el_hostname_deben_exigir_mfa():
    good = ui_app(mfa_policy(name="ok"), app_id="a")
    bad = ui_app(mfa_policy(name="mala", methods=()), app_id="b", destination="*.example.com")

    result = evaluate(good, bad)

    assert result.status is FAIL
    assert "«mala»" in result.reason


def test_destino_ilegible_que_podria_ser_el_hostname_es_unknown():
    result = evaluate(ui_app(mfa_policy(), destination=f"{UI}:8443"))

    assert (result.status, result.reason_code) == (UNKNOWN, "ui_app_unresolved")


def test_grupo_en_require_con_mfa_es_pass():
    group = {
        "id": "g1",
        "require": ["auth_method"],
        "require_groups": [],
        "require_auth_methods": ["mfa"],
    }
    policy = {
        **mfa_policy(name="g", methods=()),
        "require": ["group"],
        "require_groups": ["g1"],
    }

    assert evaluate(ui_app(policy), groups=[group]).status is PASS


def test_grupo_en_require_inexistente_es_unknown():
    policy = {**mfa_policy(name="g", methods=()), "require": ["group"], "require_groups": ["x"]}

    result = evaluate(ui_app(policy))

    assert (result.status, result.reason_code) == (UNKNOWN, "policy_unresolved")


def test_fail_gana_sobre_unknown():
    result = evaluate(
        ui_app(mfa_policy("non_identity", name="maquinas"), mfa_policy(name="sin", methods=()))
    )

    assert result.status is FAIL


@pytest.mark.parametrize("section_name", ["access_applications", "access_policies"])
def test_seccion_no_recogida_no_es_pass(section_name):
    failed = section(status="PERMISSION_DENIED", reason="403")

    result = evaluate(ui_app(mfa_policy()), **{_SECTION_ARGS[section_name]: failed})

    assert result.status is UNKNOWN
    assert (result.collection_status.value, result.reason_code) == (
        "PERMISSION_DENIED",
        "permission_denied",
    )


_SECTION_ARGS = {"access_applications": "applications", "access_policies": "policies_section"}


def test_politicas_de_la_aplicacion_no_recogidas_no_es_pass():
    result = evaluate(ui_app(mfa_policy(), policy_status="ERROR"))

    assert result.status is ERROR


def test_evidencia_anterior_sin_campos_de_mfa_es_unknown():
    entry = ui_app(mfa_policy())
    del entry["app"]["mfa_required"]

    result = evaluate(entry)

    assert (result.status, result.reason_code) == (UNKNOWN, "stale_evidence")


def test_recogida_fallida_nunca_es_pass():
    result = evaluate(ui_app(mfa_policy()), collection_status="ERROR")

    assert result.status is ERROR


def test_el_motor_registra_el_check():
    assert CHECK_FUNCTIONS[UI_ACCESS_REQUIRES_MFA] is ui_access_requires_mfa


POLICIES_PATH = f"/accounts/{ACCOUNT}/access/apps/app-1/policies"
INFO = {"page": 1, "per_page": 1000, "total_pages": 1}


def test_la_recogida_guarda_solo_los_campos_minimos_de_mfa(api, collect):
    api.set(
        POLICIES_PATH,
        envelope(
            [
                {
                    "id": "p",
                    "name": "con-mfa",
                    "decision": "allow",
                    "include": [{"email": {"email": "ana-CANARY@x.invalid"}}],
                    "require": [
                        {"auth_method": {"auth_method": "MFA"}},
                        {"auth_method": {"auth_method": {"raro": "CANARY"}}},
                        {"auth_method": "texto-CANARY"},
                    ],
                    "mfa_config": {"mfa_disabled": False, "allowed_authenticators": ["totp"]},
                },
                {"id": "q", "decision": "allow", "mfa_config": {"mfa_disabled": True}},
                {"id": "r", "decision": "allow", "mfa_config": {}},
            ],
            INFO,
        ),
    )

    policies = collect().data["sections"]["access_policies"]["items"][0]["policies"]

    assert [p["require_auth_methods"] for p in policies] == [
        ["mfa", "unreadable", "unreadable"],
        [],
        [],
    ]
    assert [p["mfa_required"] for p in policies] == [True, False, False]
    assert "CANARY" not in str(policies)


def test_la_recogida_marca_mfa_de_la_aplicacion_y_el_hostname_de_la_ui(api, collect):
    app = {
        "id": "app-1",
        "type": "self_hosted",
        "domain": UI,
        "mfa_config": {"mfa_disabled": False},
    }
    api.set(f"/accounts/{ACCOUNT}/access/apps", envelope([app], INFO))

    data = collect(ui_hostname=UI).data

    assert data["ui_hostname"] == UI
    assert data["sections"]["access_applications"]["items"][0]["mfa_required"] is True
