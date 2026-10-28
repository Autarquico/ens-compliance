"""Scenarios de la spec `collector-cloudflare` sobre la recogida real (MockTransport)."""

import httpx
import pytest

from ens_evidence.connectors.cloudflare.config import PublicRoute
from ens_evidence.models.contracts import CheckResult
from ens_evidence.models.enums import CheckStatus, CollectionStatus

from .check_data import EVIDENCE_ID, by_subject, run
from .fake_api import (
    ACCOUNT,
    TUNNEL_LOCAL,
    TUNNEL_REMOTE,
    ZONE,
    denied,
    envelope,
    failure,
)

APPS = f"/accounts/{ACCOUNT}/access/apps"
TUNNELS = f"/accounts/{ACCOUNT}/cfd_tunnel"
APP_ROUTE = f"{TUNNEL_REMOTE} app.taller-norte.example/*"
ADMIN_ROUTE = f"{TUNNEL_REMOTE} admin.taller-norte.example^/agent/.*"
LOCAL_ROUTE = f"{TUNNEL_LOCAL} panel.taller-norte.example/*"


def protect_everything(api):
    api.set(
        APPS,
        envelope(
            [
                {
                    "id": "app-1",
                    "name": "Paneles",
                    "type": "self_hosted",
                    "destinations": [
                        {"type": "public", "uri": "app.taller-norte.example"},
                        {"type": "public", "uri": "admin.taller-norte.example/agent/*"},
                        {"type": "public", "uri": "panel.taller-norte.example"},
                    ],
                }
            ],
            {"page": 1, "total_pages": 1},
        ),
    )


def test_hostname_publicado_por_tunnel_sin_aplicacion_access_da_fail(collect):
    checks = by_subject(run(collect()))

    exposed = checks[APP_ROUTE]
    assert exposed.status is CheckStatus.FAIL
    assert "ninguna aplicación Access cubre la ruta" in exposed.reason
    assert exposed.asset_id == "cf-taller"
    assert exposed.check_id == "cloudflare.tunnel_requires_access"


def test_hostname_con_aplicacion_access_de_identidad_da_pass_citando_la_evidencia(api, collect):
    protect_everything(api)

    checks = by_subject(run(collect()))

    assert checks[APP_ROUTE].status is CheckStatus.PASS
    assert checks[ADMIN_ROUTE].status is CheckStatus.PASS
    assert checks[APP_ROUTE].evidence_ids == [EVIDENCE_ID]


def test_tunel_local_cubierto_por_access_no_pasa_da_unknown_ingress_no_visible(api, collect):
    protect_everything(api)

    local = by_subject(run(collect()))[LOCAL_ROUTE]

    assert local.status is CheckStatus.UNKNOWN
    assert "ingress local no visible" in local.reason


def test_tunel_local_sin_aplicacion_que_lo_cubra_da_fail(api, collect):
    api.set(APPS, envelope([], {"page": 1, "total_pages": 1}))

    assert by_subject(run(collect()))[LOCAL_ROUTE].status is CheckStatus.FAIL


def test_el_subject_es_estable_entre_ejecuciones(collect):
    first = [c.subject for c in run(collect())]
    second = [c.subject for c in run(collect())]

    assert first == second
    assert APP_ROUTE in first


@pytest.mark.parametrize(
    "verify_reply",
    [
        pytest.param((401, failure(1000, "Invalid API Token")), id="revocado"),
        pytest.param(envelope({"id": "t", "status": "disabled"}), id="inactivo"),
    ],
)
def test_credencial_revocada_no_da_ningun_pass(api, collect, verify_reply):
    api.set(r"/.*/tokens/verify", verify_reply)

    result = collect()
    checks = run(result)

    assert result.collection_status is CollectionStatus.ERROR
    assert [c.status for c in checks] == [CheckStatus.ERROR]
    assert checks[0].collection_status is CollectionStatus.ERROR
    assert checks[0].subject is None
    assert checks[0].evidence_ids == [EVIDENCE_ID]


def test_credencial_revocada_a_mitad_de_la_recogida_no_da_ningun_pass(api, collect):
    protect_everything(api)
    verifications = []

    def verify(_request: httpx.Request) -> httpx.Response:
        verifications.append(1)
        state = "active" if len(verifications) == 1 else "disabled"
        return httpx.Response(200, json=envelope({"id": "t", "status": state}))

    api.set(r"/.*/tokens/verify", verify)

    checks = run(collect())

    assert [c.status for c in checks] == [CheckStatus.ERROR]


@pytest.mark.parametrize(
    "path",
    [
        pytest.param(APPS, id="aplicaciones"),
        pytest.param(f"{APPS}/app-1/policies", id="politicas"),
    ],
)
def test_permiso_insuficiente_en_access_da_unknown_permission_denied_nunca_pass(api, collect, path):
    protect_everything(api)
    api.set(path, denied())

    checks = by_subject(run(collect()))

    for subject in (APP_ROUTE, ADMIN_ROUTE):
        assert checks[subject].status is CheckStatus.UNKNOWN
        assert checks[subject].reason_code == "permission_denied"
        assert checks[subject].collection_status is CollectionStatus.PERMISSION_DENIED


def test_permiso_insuficiente_en_tunnels_da_un_unknown_permission_denied(api, collect):
    api.set(TUNNELS, denied())

    checks = run(collect())

    assert len(checks) == 1
    assert checks[0].status is CheckStatus.UNKNOWN
    assert checks[0].reason_code == "permission_denied"
    assert checks[0].subject is None


def test_error_en_aplicaciones_access_da_error_en_cada_ruta_nunca_pass(api, collect):
    api.set(APPS, (500, failure(1, "boom")))

    checks = run(collect())

    assert checks
    assert {c.status for c in checks if c.subject in (APP_ROUTE, ADMIN_ROUTE)} == {
        CheckStatus.ERROR
    }
    assert CheckStatus.PASS not in {c.status for c in checks}


def test_ruta_declarada_publica_da_unknown_declared_public_y_el_resto_se_evalua(collect):
    route = PublicRoute(
        hostname="admin.taller-norte.example", path="/agent/*", reason="endpoint del agente"
    )

    checks = by_subject(run(collect(public_routes=[route])))

    agent = checks[ADMIN_ROUTE]
    assert agent.status is CheckStatus.UNKNOWN
    assert agent.reason_code == "declared_public"
    assert "endpoint del agente" in agent.reason
    assert checks[APP_ROUTE].status is CheckStatus.FAIL


def test_tunel_local_con_dns_denegado_añade_unknown_de_cobertura_no_resuelta(api, collect):
    api.set(f"/zones/{ZONE}/dns_records", denied())

    checks = run(collect())

    coverage = [c for c in checks if c.subject == TUNNEL_LOCAL]
    assert len(coverage) == 1
    assert coverage[0].status is CheckStatus.UNKNOWN
    assert coverage[0].reason_code == "permission_denied"
    assert "cobertura no resuelta" in coverage[0].reason


def test_todo_resultado_valida_contra_el_modelo(collect):
    for check in run(collect()):
        assert CheckResult.model_validate(check.model_dump()) == check
