from ens_evidence.models.enums import CheckStatus

from .check_data import (
    TUNNEL,
    app,
    build,
    by_subject,
    declared,
    local,
    only,
    remote,
    rule,
    run,
    section,
    subject,
)

PASS, FAIL, UNKNOWN, ERROR = (
    CheckStatus.PASS,
    CheckStatus.FAIL,
    CheckStatus.UNKNOWN,
    CheckStatus.ERROR,
)
HOST = "app.z.example"
BYPASS = {"decision": "bypass", "include": ["everyone"], "require": []}


def test_catch_all_sin_hostname_y_http_status_no_son_rutas():
    tunnel = remote(
        rule(HOST),
        {"service": "http_status:404", "hostname": None, "path": None},
        rule("fijo.z.example", service="http_status:403"),
    )

    results = run(build([tunnel], [app("a", HOST)]))

    assert [c.subject for c in results] == [subject(HOST)]


def test_hostname_repetido_en_el_ingress_se_evalua_una_vez():
    results = run(build([remote(rule(HOST), rule(HOST))], [app("a", HOST)]))

    assert len(results) == 1


def test_tunel_remoto_con_ingress_no_recogido_da_un_resultado_del_tunel_con_su_estado():
    tunnel = remote(ingress_status="PERMISSION_DENIED", reason="HTTP 403")

    result = only(run(build([tunnel], [app("a", HOST)])))

    assert result.status is UNKNOWN
    assert result.reason_code == "permission_denied"
    assert result.subject == TUNNEL


def test_tunel_remoto_con_ingress_en_error_da_error():
    result = only(run(build([remote(ingress_status="ERROR", reason="HTTP 500")])))

    assert result.status is ERROR
    assert result.subject == TUNNEL


def test_tunel_local_cubierto_por_access_da_unknown_ingress_local_no_visible():
    result = only(run(build([local(HOST)], [app("a", HOST)])))

    assert result.status is UNKNOWN
    assert "ingress local no visible" in result.reason
    assert result.subject == subject(HOST)


def test_tunel_local_sin_nada_que_lo_cubra_da_fail():
    result = only(run(build([local(HOST)])))

    assert result.status is FAIL


def test_tunel_local_con_bypass_da_fail():
    result = only(run(build([local(HOST)], [app("a", HOST, policies=[BYPASS])])))

    assert result.status is FAIL


def test_tunel_local_con_inferencia_incompleta_añade_unknown_de_cobertura_no_resuelta():
    results = run(build([local(HOST, complete=False)]))

    checks = by_subject(results)
    assert checks[subject(HOST)].status is FAIL
    assert checks[TUNNEL].status is UNKNOWN
    assert "cobertura no resuelta" in checks[TUNNEL].reason


def test_tunel_local_sin_hostnames_inferidos_e_incompleto_nunca_da_cero_resultados():
    result = only(run(build([local(complete=False)])))

    assert result.status is UNKNOWN
    assert result.subject == TUNNEL


def test_tunel_local_con_dns_no_success_añade_unknown_con_el_estado_de_la_recogida():
    dns = section(status="PERMISSION_DENIED", reason="HTTP 403")

    result = only(run(build([local(complete=False)], dns=dns)))

    assert result.status is UNKNOWN
    assert result.reason_code == "permission_denied"
    assert result.collection_status.value == "PERMISSION_DENIED"


def test_tunel_local_con_dns_en_error_da_error():
    dns = section(status="ERROR", reason="HTTP 500")

    result = only(run(build([local(complete=False)], dns=dns)))

    assert result.status is ERROR


def test_tunel_local_con_zonas_del_inventario_no_visibles_añade_unknown_de_cobertura():
    zones = section(missing_from_token=["oculta.example"])

    results = run(build([local(HOST)], [app("a", HOST)], zones=zones))

    checks = by_subject(results)
    assert checks[TUNNEL].status is UNKNOWN
    assert "no ve 1 zona" in checks[TUNNEL].reason


def test_tunel_local_con_inferencia_completa_no_añade_unknown_de_cobertura():
    results = run(build([local(HOST)], [app("a", HOST)]))

    assert [c.subject for c in results] == [subject(HOST)]


def test_ruta_totalmente_declarada_publica_da_unknown_declared_public_sin_evaluar_access():
    result = only(
        run(build([remote(rule(HOST, "^/agent/.*"))], public_routes=[declared(HOST, "/agent/*")]))
    )

    assert result.status is UNKNOWN
    assert result.reason_code == "declared_public"
    assert "agente público" in result.reason
    assert result.collection_status.value == "SUCCESS"


def test_declarar_el_hostname_entero_cubre_cualquier_path_de_la_ruta():
    result = only(run(build([remote(rule(HOST, "^/x/.*"))], public_routes=[declared(HOST)])))

    assert result.reason_code == "declared_public"


def test_solo_el_subconjunto_declarado_es_unknown_y_el_resto_del_hostname_se_evalua():
    tunnel = remote(rule(HOST, "^/agent/.*"), rule(HOST, "^/panel/.*"))

    checks = by_subject(
        run(
            build(
                [tunnel], [app("a", f"{HOST}/panel/*")], public_routes=[declared(HOST, "/agent/*")]
            )
        )
    )

    assert checks[subject(HOST, "^/agent/.*")].reason_code == "declared_public"
    assert checks[subject(HOST, "^/panel/.*")].status is PASS


def test_ruta_declarada_en_otro_hostname_no_afecta():
    result = only(
        run(build([remote(rule(HOST))], public_routes=[declared("otro.z.example", "/*")]))
    )

    assert result.status is FAIL
    assert result.reason_code is None


def test_app_publica_declarada_dentro_de_una_ruta_protegida_se_excluye_y_se_cita():
    apps = [app("general", f"{HOST}/*"), app("agente", f"{HOST}/agent/*", policies=[BYPASS])]

    checks = by_subject(
        run(build([remote(rule(HOST))], apps, public_routes=[declared(HOST, "/agent/*")]))
    )

    assert checks[subject(HOST)].status is PASS
    assert "excluido el subconjunto declarado público" in checks[subject(HOST)].reason
    assert checks[subject(HOST, "/agent/*")].status is UNKNOWN
    assert checks[subject(HOST, "/agent/*")].reason_code == "declared_public"


def test_la_misma_app_publica_sin_declarar_hace_fallar_la_ruta_protegida():
    apps = [app("general", f"{HOST}/*"), app("agente", f"{HOST}/agent/*", policies=[BYPASS])]

    result = only(run(build([remote(rule(HOST))], apps)))

    assert result.status is FAIL


def test_regla_de_ingress_con_path_y_sin_hostname_da_unknown_y_no_se_descarta():
    tunnel = remote(
        rule("app.z.example"), rule(None, "^/api"), rule(None, service="http_status:404")
    )

    results = by_subject(run(build([tunnel], [app("a", "app.z.example")])))

    assert results[f"{TUNNEL} (sin hostname)^/api"].status is UNKNOWN
    assert results[subject("app.z.example")].status is PASS
    assert len(results) == 2


def test_catch_all_final_con_origen_real_da_unknown_y_http_status_no_deja_resultado():
    apps = [app("a", "app.z.example")]
    origin = remote(rule("app.z.example"), rule(None, service="http://localhost:8080"))
    fixed = remote(rule("app.z.example"), rule(None, service="http_status:404"))

    with_origin = by_subject(run(build([origin], apps)))
    with_fixed = by_subject(run(build([fixed], apps)))

    assert with_origin[f"{TUNNEL} (sin hostname)/*"].status is UNKNOWN
    assert len(with_origin) == 2
    assert len(with_fixed) == 1


def test_hostname_de_la_ruta_en_mayusculas_se_compara_en_minusculas_con_destinos_comodin():
    apps = [app("a", "app.*.example")]

    result = only(run(build([remote(rule("APP.Z.example"))], apps)))

    assert result.status is UNKNOWN
    assert "no interpretables" in result.reason


def test_sin_tuneles_no_hay_rutas_que_evaluar_ni_pass():
    assert run(build([])) == []
