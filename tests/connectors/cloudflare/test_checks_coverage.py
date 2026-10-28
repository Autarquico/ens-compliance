import pytest

from ens_evidence.models.enums import CheckStatus

from .check_data import app, build, by_subject, only, remote, rule, run, subject

HOST = "app.z.example"
PASS, FAIL, UNKNOWN = CheckStatus.PASS, CheckStatus.FAIL, CheckStatus.UNKNOWN


def evaluate(destination, *, hostname=HOST, path=None, **app_options):
    return only(run(build([remote(rule(hostname, path))], [app("a", destination, **app_options)])))


@pytest.mark.parametrize(
    ("destination", "hostname", "path"),
    [
        pytest.param("app.z.example", HOST, None, id="sin-path-cubre-todo"),
        pytest.param("app.z.example/*", HOST, None, id="path-comodin"),
        pytest.param("https://app.z.example/*", HOST, None, id="con-esquema"),
        pytest.param("*.z.example", HOST, None, id="comodin-un-nivel"),
        pytest.param("*.z.example", "*.z.example", None, id="comodin-cubre-comodin"),
        pytest.param("APP.Z.EXAMPLE.", HOST, None, id="mayusculas-y-punto-final"),
        pytest.param("app.z.example/a/*", HOST, "^/a/.*", id="prefijo-igual"),
        pytest.param("app.z.example/a/*", HOST, "^/a/x", id="prefijo-cubre-subruta"),
        pytest.param("app.z.example/a", HOST, "^/a/.*", id="sin-comodin-hereda-en-subpaths"),
    ],
)
def test_la_ruta_cubierta_por_entero_da_pass(destination, hostname, path):
    assert evaluate(destination, hostname=hostname, path=path).status is PASS


@pytest.mark.parametrize(
    ("destination", "hostname", "path"),
    [
        pytest.param("*.z.example", "z.example", None, id="comodin-no-cubre-apex"),
        pytest.param("*.z.example", "a.b.z.example", None, id="comodin-no-cubre-dos-niveles"),
        pytest.param("z.example", HOST, None, id="apex-no-cubre-subdominio"),
        pytest.param("otro.z.example", HOST, None, id="otro-host"),
        pytest.param("app.z.example/a/*", HOST, "^/b/.*", id="otro-path"),
        pytest.param("x.*.example", HOST, None, id="comodin-interno-que-no-casa"),
    ],
)
def test_la_ruta_sin_aplicacion_que_la_solape_da_fail(destination, hostname, path):
    result = evaluate(destination, hostname=hostname, path=path)

    assert result.status is FAIL
    assert "ninguna aplicación Access cubre la ruta" in result.reason


@pytest.mark.parametrize(
    ("destination", "hostname", "path"),
    [
        pytest.param("app.z.example/admin/*", HOST, None, id="app-solo-cubre-subruta"),
        pytest.param("app.z.example/a/*", HOST, "/a", id="a-prefijo-no-cubre-a"),
        pytest.param("app.z.example/a", HOST, None, id="app-exacta-sobre-ruta-amplia"),
        pytest.param("app.z.example", "*.z.example", None, id="app-de-host-sobre-ruta-comodin"),
    ],
)
def test_la_cobertura_parcial_da_unknown(destination, hostname, path):
    result = evaluate(destination, hostname=hostname, path=path)

    assert result.status is UNKNOWN
    assert "cobertura parcial" in result.reason


@pytest.mark.parametrize(
    "destination",
    [
        pytest.param("app*.z.example/*", id="comodin-de-host-no-interpretable"),
        pytest.param("app.*.example", id="comodin-intermedio-de-host"),
        pytest.param("app.z.example/a/*/b", id="comodin-en-medio-del-path"),
    ],
)
def test_un_destino_no_interpretable_que_podria_solapar_da_unknown(destination):
    result = evaluate(destination)

    assert result.status is UNKNOWN
    assert "no interpretables" in result.reason


def test_un_destino_no_interpretable_impide_el_pass_aunque_otra_app_cubra_la_ruta():
    results = run(
        build(
            [remote(rule(HOST))],
            [app("cubre", "app.z.example/*"), app("rara", "app.z.example/a/*/b")],
        )
    )

    assert only(results).status is UNKNOWN


def test_la_app_mas_especifica_solapada_que_no_protege_da_fail():
    bypass = {"decision": "bypass", "include": ["everyone"], "require": []}
    results = run(
        build(
            [remote(rule(HOST))],
            [
                app("general", "app.z.example/*"),
                app("publica", "app.z.example/api/*", policies=[bypass]),
            ],
        )
    )

    result = only(results)
    assert result.status is FAIL
    assert "más específica" in result.reason
    assert "publica" in result.reason


def test_la_app_mas_especifica_solapada_que_protege_deja_pass():
    results = run(
        build(
            [remote(rule(HOST))],
            [app("general", "app.z.example/*"), app("admin", "app.z.example/admin/*")],
        )
    )

    assert only(results).status is PASS


def test_un_bypass_menos_especifico_tapado_por_una_app_protectora_es_unknown():
    bypass = {"decision": "bypass", "include": ["everyone"], "require": []}
    results = run(
        build(
            [remote(rule(HOST, "^/api/.*"))],
            [
                app("general", "app.z.example/*", policies=[bypass]),
                app("api", "app.z.example/api/*"),
            ],
        )
    )

    assert only(results).status is UNKNOWN


def test_un_bypass_de_comodin_tapado_por_el_host_exacto_es_unknown():
    bypass = {"decision": "bypass", "include": ["everyone"], "require": []}
    results = run(
        build(
            [remote(rule(HOST))],
            [app("exacto", "app.z.example/*"), app("comodin", "*.z.example/*", policies=[bypass])],
        )
    )

    assert only(results).status is UNKNOWN


def test_apex_y_subdominio_del_mismo_tunel_se_evaluan_por_separado():
    results = run(
        build(
            [remote(rule("z.example"), rule(HOST), rule("a.b.z.example"))],
            [app("comodin", "*.z.example/*")],
        )
    )

    checks = by_subject(results)
    assert checks[subject("z.example")].status is FAIL
    assert checks[subject(HOST)].status is PASS
    assert checks[subject("a.b.z.example")].status is FAIL


def test_cada_ruta_distinta_del_mismo_hostname_tiene_su_propio_resultado():
    results = run(
        build(
            [remote(rule(HOST, "^/a/.*"), rule(HOST, "^/b/.*"), rule(HOST))],
            [app("a", "app.z.example/a/*")],
        )
    )

    checks = by_subject(results)
    assert checks[subject(HOST, "^/a/.*")].status is PASS
    assert checks[subject(HOST, "^/b/.*")].status is FAIL
    assert checks[subject(HOST, "/*")].status is UNKNOWN
