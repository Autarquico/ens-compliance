import pytest

from ens_evidence.models.enums import CheckStatus

from .check_data import app, build, only, remote, rule, run
from .fake_api import ACCOUNT, TUNNEL_LOCAL, TUNNEL_REMOTE, envelope

PASS, FAIL, UNKNOWN = CheckStatus.PASS, CheckStatus.FAIL, CheckStatus.UNKNOWN
HOST = "app.z.example"
BYPASS = {"decision": "bypass", "include": ["everyone"], "require": []}
APPS = f"/accounts/{ACCOUNT}/access/apps"
ROUTE = "app.taller-norte.example"


def evaluate(*apps):
    return only(run(build([remote(rule(HOST))], list(apps))))


@pytest.mark.parametrize("app_type", ["self_hosted", "ssh", "vnc", "rdp"])
def test_los_tipos_que_aplican_politica_de_ruta_pueden_dar_pass(app_type):
    assert evaluate(app("a", HOST, type=app_type)).status is PASS


def test_una_app_bookmark_se_ignora_aunque_tenga_bypass():
    result = evaluate(app("marcador", HOST, type="bookmark", policies=[BYPASS]))

    assert result.status is FAIL
    assert "ninguna aplicación Access cubre la ruta" in result.reason


def test_una_app_bookmark_no_cuenta_como_cobertura():
    result = evaluate(app("marcador", HOST, type="bookmark"))

    assert result.status is FAIL


@pytest.mark.parametrize("app_type", ["saas", "app_launcher", "warp", "infrastructure", None])
def test_otro_tipo_de_app_solapado_da_unknown_aunque_otra_cubra(app_type):
    result = evaluate(app("general", HOST), app("rara", HOST, type=app_type))

    assert result.status is UNKNOWN
    assert "tipo de aplicación" in result.reason


def test_otro_tipo_de_app_que_no_solapa_la_ruta_no_cambia_el_resultado():
    result = evaluate(app("general", HOST), app("saas", "otro.z.example", type="saas"))

    assert result.status is PASS


def apps_reply(app_fields):
    return envelope(
        [{"id": "app-1", "name": "Paneles", "type": "self_hosted", **app_fields}],
        {"page": 1, "total_pages": 1},
    )


def route_result(collect):
    return next(r for r in run(collect()) if r.subject == f"{TUNNEL_REMOTE} {ROUTE}/*")


def test_destino_publico_de_destinations_cubre_la_ruta_y_el_privado_no_cuenta(api, collect):
    api.set(
        APPS,
        apps_reply(
            {
                "destinations": [
                    {"type": "private", "cidr": "10.0.0.0/8"},
                    {"type": "public", "uri": ROUTE},
                ]
            }
        ),
    )

    assert route_result(collect).status is PASS


def test_app_solo_con_destinos_privados_no_cubre_ni_siquiera_con_domain_heredado(api, collect):
    api.set(
        APPS,
        apps_reply({"destinations": [{"type": "private", "cidr": "10.0.0.0/8"}], "domain": ROUTE}),
    )

    result = route_result(collect)

    assert result.status is FAIL
    assert "ninguna aplicación Access cubre la ruta" in result.reason


def test_self_hosted_domains_cubre_la_ruta_si_no_hay_destinations(api, collect):
    api.set(APPS, apps_reply({"self_hosted_domains": [ROUTE]}))

    assert route_result(collect).status is PASS


def test_domain_heredado_cubre_la_ruta_si_no_hay_nada_mas(api, collect):
    api.set(APPS, apps_reply({"domain": ROUTE}))

    assert route_result(collect).status is PASS


def test_destinations_manda_sobre_self_hosted_domains_y_domain(api, collect):
    api.set(
        APPS,
        apps_reply(
            {
                "destinations": [{"type": "public", "uri": "otro.taller-norte.example"}],
                "self_hosted_domains": [ROUTE],
                "domain": ROUTE,
            }
        ),
    )

    assert route_result(collect).status is FAIL


def test_el_tunel_local_no_se_confunde_con_el_remoto_al_buscar_la_ruta(collect):
    subjects = {r.subject for r in run(collect())}

    assert f"{TUNNEL_LOCAL} panel.taller-norte.example/*" in subjects
    assert f"{TUNNEL_REMOTE} {ROUTE}/*" in subjects
