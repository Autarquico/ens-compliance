import pytest

from ens_evidence.connectors.cloudflare.sections import selector_kinds
from ens_evidence.models.enums import CheckStatus

from .check_data import app, build, declared, only, policy, remote, rule, run

HOST = "app.z.example"
BYPASS = (policy("bypass", include=("everyone",)),)


def evaluate(apps, path):
    return only(run(build([remote(rule(HOST, path))], apps)))


def test_una_app_exacta_no_tapa_un_bypass_de_subpaths_con_comodin():
    result = evaluate([app("a", f"{HOST}/a"), app("b", f"{HOST}/a/*", policies=BYPASS)], "^/a/.*")

    assert result.status is CheckStatus.FAIL


def test_una_app_exacta_no_tapa_un_bypass_con_prefijo_mas_largo():
    result = evaluate([app("a", f"{HOST}/a"), app("b", f"{HOST}/a/x*", policies=BYPASS)], "^/a/.*")

    assert result.status is CheckStatus.FAIL


def test_una_app_exacta_no_tapa_un_bypass_mas_especifica_sobre_la_ruta_raiz_del_path():
    result = evaluate([app("g", f"{HOST}/a"), app("b", f"{HOST}/a/x*", policies=BYPASS)], "^/a/")

    assert result.status is CheckStatus.FAIL


@pytest.mark.parametrize(
    "destination",
    [f"{HOST}:443", f"{HOST}.:443", f"{HOST}:", "::basura"],
)
def test_un_bypass_con_destino_no_interpretable_no_suma_hacia_pass(destination):
    result = evaluate([app("a", HOST), app("b", destination, policies=BYPASS)], "^/x")

    assert result.status is CheckStatus.UNKNOWN


@pytest.mark.parametrize("destination", ["other.example:443", "other.example.:", "other.example."])
def test_un_destino_no_interpretable_de_otro_host_no_impide_el_pass(destination):
    result = evaluate([app("a", HOST), app("b", destination, policies=BYPASS)], "^/x")

    assert result.status is CheckStatus.PASS


def test_bypass_en_el_host_tapado_por_una_app_mas_especifica_da_unknown():
    result = evaluate(
        [app("b", HOST, policies=BYPASS), app("admin", f"{HOST}/admin")], "^/admin/.*"
    )

    assert result.status is CheckStatus.UNKNOWN
    assert "no demostrable" in result.reason


def test_solo_apps_protectoras_solapadas_dan_pass():
    result = evaluate([app("g", HOST), app("admin", f"{HOST}/admin/*")], "^/admin/.*")

    assert result.status is CheckStatus.PASS


def test_una_app_bypass_en_un_path_sin_comodin_alcanza_sus_subpaths():
    result = evaluate([app("g", HOST), app("b", f"{HOST}/public", policies=BYPASS)], "^/public/x")

    assert result.status is CheckStatus.FAIL


def test_una_app_de_identidad_en_un_path_sin_comodin_cubre_sus_subpaths():
    result = evaluate([app("a", f"{HOST}/a")], "^/a/.*")

    assert result.status is CheckStatus.PASS


def test_un_path_sin_comodin_no_cubre_un_hermano_con_el_mismo_prefijo():
    result = evaluate([app("a", f"{HOST}/a")], "^/ab")

    assert result.status is CheckStatus.FAIL
    assert "ninguna aplicación" in result.reason


def test_un_bypass_en_un_path_hermano_no_afecta_a_una_app_que_cubre_la_ruta():
    result = evaluate([app("g", HOST), app("b", f"{HOST}/a", policies=BYPASS)], "^/ab")

    assert result.status is CheckStatus.PASS


@pytest.mark.parametrize(
    ("destination", "path"),
    [
        pytest.param(f"{HOST}/", "^/x", id="host-con-barra-final"),
        pytest.param(f"{HOST}/a/", "^/a/x", id="path-con-barra-final"),
    ],
)
def test_un_destino_access_con_barra_final_hereda_como_sin_ella(destination, path):
    assert evaluate([app("a", destination)], path).status is CheckStatus.PASS


def test_una_ruta_publica_declarada_sin_comodin_cubre_tambien_sus_subpaths():
    result = only(
        run(
            build(
                [remote(rule(HOST, "^/agent/x"))],
                [app("a", HOST)],
                public_routes=[declared(HOST, "/agent")],
            )
        )
    )

    assert result.status is CheckStatus.UNKNOWN
    assert result.reason_code == "declared_public"


@pytest.mark.parametrize("selector", ["github-organization", "azureAD"])
def test_un_selector_de_identidad_con_guion_o_mayusculas_conserva_su_nombre_hasta_el_check(
    selector,
):
    assert selector_kinds([{selector: {"id": "x"}}]) == [selector]
    allow = (policy("allow", [selector]),)

    assert evaluate([app("a", HOST, policies=allow)], "^/x").status is CheckStatus.PASS
