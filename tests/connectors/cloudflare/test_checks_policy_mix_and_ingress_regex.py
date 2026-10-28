import pytest

from ens_evidence.models.enums import CheckStatus

from .check_data import app, build, only, policy, remote, rule, run

PASS, FAIL, UNKNOWN = CheckStatus.PASS, CheckStatus.FAIL, CheckStatus.UNKNOWN
HOST = "app.z.example"
ALLOW_EMAIL = policy("allow", ["email"])


def evaluate(tunnel_rule, *apps):
    return only(run(build([remote(tunnel_rule)], list(apps))))


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        pytest.param(policy("non_identity", ["everyone"]), FAIL, id="non_identity-everyone"),
        pytest.param(policy("non_identity", ["ip"]), UNKNOWN, id="non_identity-ip"),
        pytest.param(policy("non_identity", ["geo"]), UNKNOWN, id="non_identity-geo"),
        pytest.param(policy("allow", ["ip"]), UNKNOWN, id="allow-ip-junto-a-allow-identidad"),
        pytest.param(policy("non_identity", ["service_token"]), PASS, id="non_identity-token"),
    ],
)
def test_politica_que_admite_sin_identidad_junto_a_allow_de_identidad(extra, expected):
    result = evaluate(rule(HOST), app("a", HOST, policies=(ALLOW_EMAIL, extra)))

    assert result.status is expected


@pytest.mark.parametrize(
    "path",
    [
        pytest.param("/admin", id="sin-anclar"),
        pytest.param("admin", id="sin-barra"),
        pytest.param("^/admin$", id="con-dolar"),
        pytest.param("^/ad.in", id="metacaracter"),
        pytest.param("/admin/.*", id="sin-anclar-con-comodin"),
    ],
)
def test_path_de_ingress_regex_no_anclada_no_se_trata_como_prefijo(path):
    result = evaluate(rule(HOST, path), app("a", f"{HOST}/admin*"))

    assert result.status is not PASS


@pytest.mark.parametrize("path", ["^/admin", "^/admin.*", "^/admin/.*"])
def test_path_de_ingress_anclado_y_literal_es_prefijo(path):
    result = evaluate(rule(HOST, path), app("a", f"{HOST}/admin*"))

    assert result.status is PASS


def test_path_de_ingress_no_anclado_exige_cubrir_todo_el_host():
    result = evaluate(rule(HOST, "/admin"), app("a", HOST))

    assert result.status is PASS


@pytest.mark.parametrize("host_first", [True, False])
def test_bypass_con_path_mas_especifico_y_host_comodin_no_se_descarta(host_first):
    protected = app("a", HOST)
    bypass = app("b", "*.z.example/admin*", policies=(policy("bypass", ["everyone"]),))
    apps = (protected, bypass) if host_first else (bypass, protected)

    result = evaluate(rule(HOST), *apps)

    assert result.status is FAIL
    assert "bypass" in result.reason


def test_cobertura_con_precedencia_host_path_ambigua_evalua_ambas_aplicaciones():
    protected = app("a", HOST)
    bypass = app("b", "*.z.example/admin*", policies=(policy("bypass", ["everyone"]),))

    result = evaluate(rule(HOST, "^/admin/panel"), protected, bypass)

    assert result.status is FAIL


def test_aplicacion_no_demostrable_que_solapa_parte_de_la_ruta_da_unknown():
    protected = app("a", HOST)
    opaque = app("b", f"{HOST}/admin*", policies=(policy("allow", ["ip"]),))

    result = evaluate(rule(HOST), protected, opaque)

    assert result.status is UNKNOWN
