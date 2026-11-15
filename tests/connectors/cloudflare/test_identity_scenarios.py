"""Scenarios de la spec `access-identity-guide` de punta a punta: recogida real contra la API
simulada y después `cloudflare.access_idp_restricted` tal como lo registra el motor."""

from pathlib import Path

import pytest

from ens_evidence.checks.registry import CHECK_FUNCTIONS
from ens_evidence.connectors.cloudflare.checks import ACCESS_IDP_RESTRICTED
from ens_evidence.connectors.cloudflare.config import PublicRoute
from ens_evidence.models.contracts import CheckResult
from ens_evidence.models.enums import CheckStatus

from .check_data import EVIDENCE_ID
from .fake_api import ACCOUNT, NOW, TOKEN, denied, envelope, failure, fixture
from .test_no_secrets import CANARY, artifact

APPS = f"/accounts/{ACCOUNT}/access/apps"
IDPS = f"/accounts/{ACCOUNT}/access/identity_providers"
TUNNELS = f"/accounts/{ACCOUNT}/cfd_tunnel"
WEB_POLICIES = f"/accounts/{ACCOUNT}/access/apps/web/policies"
RUNBOOK = (
    Path(__file__).resolve().parents[3]
    / "docs"
    / "runbooks"
    / "acceso-con-proveedor-de-identidad.md"
)
PAGE = {"page": 1, "total_pages": 1}
PASS, FAIL, UNKNOWN = CheckStatus.PASS, CheckStatus.FAIL, CheckStatus.UNKNOWN

OTP = {"id": "idp-otp", "name": "Código por correo", "type": "onetimepin"}
WORKSPACE = {"id": "idp-ws", "name": "Workspace pyme", "type": "google-apps"}
SECRETS = {
    "config": {"client_id": "cid-CANARY", "client_secret": "secret-CANARY"},
    "scim_config": {"secret": "scim-CANARY"},
}


def web_app(allowed=(), domain="app.taller-norte.example", **extra):
    return {
        "id": "web",
        "name": "Web de la caja",
        "type": "self_hosted",
        "domain": domain,
        "destinations": [{"type": "public", "uri": domain}],
        "allowed_idps": allowed,
        **extra,
    }


def allow(*include, **extra):
    return {"id": "pol", "decision": "allow", "precedence": 1, "include": list(include), **extra}


def via(idp_id):
    return {"login_method": {"id": idp_id}}


@pytest.fixture
def setup(api):
    """Instala aplicación, políticas y proveedores de la cuenta."""

    def install(app=None, policies=None, providers=(OTP, WORKSPACE)):
        api.set(APPS, envelope([app or web_app()], PAGE))
        api.set(WEB_POLICIES, envelope(policies or [allow(via("idp-ws"))], PAGE))
        api.set(IDPS, envelope([{**p, **SECRETS} for p in providers], PAGE))

    return install


@pytest.fixture
def check():
    def evaluate(result) -> list[CheckResult]:
        results = CHECK_FUNCTIONS[ACCESS_IDP_RESTRICTED](result, EVIDENCE_ID, NOW)
        for each in results:
            CheckResult.model_validate(each.model_dump())
        return results

    return evaluate


def of_app(results, app_id="web"):
    (found,) = [r for r in results if r.subject == app_id]
    return found


def globals_of(results):
    return [r for r in results if r.subject is None]


def test_scenario_codigo_por_correo_da_fail_con_el_nombre_de_la_app_y_el_metodo(
    setup, collect, check
):
    setup(policies=[allow(via("idp-otp"), via("idp-ws"))])

    found = of_app(check(collect()))

    assert found.status is FAIL
    assert "Web de la caja" in found.reason or "«web»" in found.reason
    assert "onetimepin" in found.reason
    assert "Código por correo" in found.reason
    assert found.evidence_ids == [EVIDENCE_ID]


def test_scenario_la_guia_explica_como_quitar_el_codigo_por_correo():
    text = RUNBOOK.read_text()

    assert "## 3. Exigirlo en cada aplicación" in text
    assert "**Quita el código de un solo uso por correo** (`onetimepin`)" in text
    assert "`allowed_idps`" in text


def test_scenario_app_con_solo_el_proveedor_de_la_pyme_no_da_fail(setup, collect, check):
    setup(app=web_app(allowed=["idp-ws"]))

    found = of_app(check(collect()))

    assert found.status is PASS
    assert "Workspace pyme" in found.reason


def test_allowed_idps_vacio_con_codigo_por_correo_en_la_cuenta_da_fail(setup, collect, check):
    setup(app=web_app(allowed=[]), policies=[allow({"everyone": {}})])

    assert of_app(check(collect())).status is FAIL


@pytest.mark.parametrize("allowed", ["absent", None])
def test_allowed_idps_ausente_o_nulo_no_da_pass(setup, collect, check, allowed):
    app = web_app(allowed=allowed)
    if allowed == "absent":
        del app["allowed_idps"]
    setup(app=app, providers=[WORKSPACE], policies=[allow({"everyone": {}})])

    found = of_app(check(collect()))

    assert found.status is UNKNOWN
    assert found.reason_code == "idp_review"


def test_scenario_sin_proveedores_en_la_cuenta_es_unknown_y_dice_que_falta(setup, collect, check):
    setup(providers=[])

    (found,) = globals_of(check(collect()))

    assert found.status is UNKNOWN
    assert found.reason_code == "no_identity_providers"
    assert "proveedores de identidad" in found.reason


def test_scenario_sin_permiso_para_leer_proveedores_es_unknown_con_el_permiso(
    api, setup, collect, check
):
    setup()
    api.set(IDPS, denied())

    (found,) = globals_of(check(collect()))

    assert found.status is UNKNOWN
    assert found.reason_code == "permission_denied"
    assert "Access: Organizations, Identity Providers, and Groups Read" in found.reason
    assert not [r for r in check(collect()) if r.status is PASS]


@pytest.mark.parametrize("missing", ["sin_proveedores", "sin_permiso"])
def test_scenario_sin_proveedores_o_sin_permiso_tambien_si_no_hay_apps_en_el_alcance(
    api, setup, collect, check, missing
):
    setup(app=web_app(domain="otro.example"))
    api.set(TUNNELS, envelope([fixture("tunnels")["result"][0]], PAGE))
    assert check(collect()) == [], "la app no está en el alcance"
    api.set(IDPS, envelope([], PAGE) if missing == "sin_proveedores" else denied())

    results = check(collect())

    assert results
    assert all(r.status is UNKNOWN for r in results)


def test_scenario_el_runbook_da_el_acceso_de_emergencia_registrado_y_revisado():
    text = RUNBOOK.read_text()
    emergency = text.split("## 6. Si el proveedor cae: acceso de emergencia")[1].split("## 7.")[0]

    assert "La web **sigue cerrada**" in emergency
    assert emergency.index("SSH con clave") < emergency.index("código por correo")
    assert "una o dos direcciones con nombre" in emergency
    assert "Nunca «todos», nunca un dominio entero, nunca `Bypass`" in emergency
    assert "ens-evidence incident new" in emergency
    assert "**retira** el código por correo" in emergency
    assert "revisa lo ocurrido y cierra el incidente" in emergency


def test_politica_reutilizable_con_codigo_por_correo_en_la_lista_da_fail(
    api, setup, collect, check
):
    setup()
    api.set(
        WEB_POLICIES,
        envelope(
            [
                allow(via("idp-ws")),
                allow(via("idp-otp"), id="reutilizable", reusable=True, uid="uid-1"),
            ],
            PAGE,
        ),
    )

    assert of_app(check(collect())).status is FAIL


def test_fallo_en_una_pagina_posterior_de_proveedores_nunca_es_pass(api, setup, collect, check):
    setup()
    first = envelope([{**WORKSPACE, **SECRETS}], {"page": 1, "per_page": 1, "total_pages": 2})
    api.set(IDPS, [first, (500, failure(1001, "boom-CANARY"))])
    result = collect()

    results = check(result)

    assert results
    assert PASS not in {r.status for r in results}
    assert CANARY not in artifact(result)
    assert all(CANARY not in r.reason for r in results)


def test_evidencia_antigua_sin_los_campos_nuevos_es_unknown_recoge_de_nuevo(setup, collect, check):
    setup()
    result = collect()
    for item in result.data["sections"]["access_applications"]["items"]:
        item.pop("allowed_idps", None)
        item.pop("allowed_idps_form", None)

    found = of_app(check(result))

    assert found.status is UNKNOWN
    assert found.reason_code == "stale_evidence"
    assert "recoge de nuevo" in found.reason


def test_evidencia_antigua_sin_seccion_de_proveedores_es_unknown(setup, collect, check):
    setup()
    result = collect()
    del result.data["sections"]["identity_providers"]

    results = check(result)

    assert results
    assert {r.status for r in results} == {UNKNOWN}
    assert {r.reason_code for r in results} == {"stale_evidence"}


def test_ningun_secreto_llega_al_artefacto_ni_a_los_motivos(setup, collect, check):
    setup(policies=[allow(via("idp-otp"), {"email": {"email": "ana-CANARY@example.invalid"}})])
    result = collect()

    results = check(result)

    assert results
    for text in [artifact(result), *(r.reason for r in results)]:
        assert CANARY not in text
        assert TOKEN not in text
        assert "client_secret" not in text


@pytest.mark.parametrize(
    "scenario",
    ["sin_permiso_proveedores", "sin_proveedores", "politicas_denegadas", "apps_denegadas"],
)
def test_con_cualquier_entrada_ausente_ningun_resultado_es_pass(
    api, setup, collect, check, scenario
):
    setup(app=web_app(allowed=["idp-ws"]))
    if scenario == "sin_permiso_proveedores":
        api.set(IDPS, denied())
    elif scenario == "sin_proveedores":
        api.set(IDPS, envelope([], PAGE))
    elif scenario == "politicas_denegadas":
        api.set(WEB_POLICIES, denied())
    else:
        api.set(APPS, denied())

    results = check(collect())

    assert results
    assert PASS not in {r.status for r in results}


def test_ruta_declarada_publica_queda_fuera_y_no_genera_resultado_para_esa_app(
    setup, collect, check
):
    setup(app=web_app(allowed=["idp-otp"]))

    results = check(
        collect(
            public_routes=[
                PublicRoute(hostname="app.taller-norte.example", path="/*", reason="agente")
            ]
        )
    )

    assert not [r for r in results if r.subject == "web"]
