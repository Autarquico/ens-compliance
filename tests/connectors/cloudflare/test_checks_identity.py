"""Tabla de verdad de `cloudflare.access_idp_restricted`."""

import pytest

from ens_evidence.models.enums import CheckStatus

from .check_data import (
    HOST,
    build_identity,
    declared,
    iapp,
    idp,
    igroup,
    ipolicy,
    local,
    only,
    remote,
    rule,
    run,
    run_identity,
    section,
    sel,
)

PASS, FAIL, UNKNOWN = CheckStatus.PASS, CheckStatus.FAIL, CheckStatus.UNKNOWN
ERROR = CheckStatus.ERROR
OTP = idp("otp", "onetimepin", "Código por correo")
WS = idp("ws", "google-apps", "Workspace pyme")
AZ = idp("az", "azureAD", "Entra pyme")
SAML = idp("sm", "saml", "SAML interno")
GOOGLE = idp("gg", "google", "Google personal")
ACCOUNT = [OTP, WS]


def evaluate(*policies, idps=ACCOUNT, allowed=(), groups=(), **kwargs):
    app = iapp("a", allowed=allowed, policies=policies, **kwargs)
    return only(run_identity(build_identity([app], idps, groups=groups)))


def allow(*include, require=(), exclude=()):
    return ipolicy("allow", include, require, exclude)


def login(idp_id):
    return sel("login_method", idp=idp_id)


def assert_result(result, status, code=None, *fragments):
    assert (result.status, result.reason_code) == (status, code), result.reason
    for fragment in fragments:
        assert fragment in result.reason


def test_codigo_por_correo_es_fail_con_app_y_metodo():
    result = evaluate(allow(login("otp"), login("ws")))

    assert_result(result, FAIL, "otp_or_consumer_idp", "«a»", "Código por correo", "onetimepin")
    assert result.subject == "a"


@pytest.mark.parametrize("bad", [OTP, GOOGLE, idp("fb", "facebook"), idp("li", "linkedin")])
def test_otp_y_proveedores_de_consumo_son_fail(bad):
    result = evaluate(allow(login(bad["id"])), idps=[bad, WS])

    assert_result(result, FAIL, "otp_or_consumer_idp")


def test_allowed_idps_vacio_admite_todos_y_con_otp_en_la_cuenta_es_fail():
    result = evaluate(allow(sel("email_domain")), allowed=[])

    assert_result(result, FAIL, "otp_or_consumer_idp", "Código por correo")


@pytest.mark.parametrize("form", ["absent", None])
def test_allowed_idps_ausente_o_nulo_nunca_es_pass(form):
    for idps in (ACCOUNT, [WS]):
        result = evaluate(allow(sel("everyone")), idps=idps, allowed=form)

        assert_result(result, UNKNOWN, "idp_review", "allowed_idps", "revísalo en la consola")


@pytest.mark.parametrize("form", ["absent", None, "malformed"])
def test_bypass_confirmado_es_fail_aunque_allowed_idps_no_se_sepa(form):
    result = evaluate(ipolicy("bypass", [sel("everyone")]), allowed=form)

    assert_result(result, FAIL, "bypass", "deja entrar sin identidad")


def test_allowed_idps_vacio_sin_otp_en_la_cuenta_y_un_directorio_es_pass():
    result = evaluate(allow(sel("email_domain")), idps=[WS], allowed=[])

    assert_result(result, PASS, None, "solo admite el proveedor de directorio Workspace pyme")
    assert "google-apps" in result.reason
    assert "doble factor" in result.reason


def test_allowed_idps_explicito_sin_otp_es_pass():
    result = evaluate(allow(sel("everyone")), allowed=["ws"])

    assert_result(result, PASS)


def test_everyone_y_email_con_otp_admitido_es_fail():
    for selector in (sel("everyone"), sel("email"), sel("email_domain")):
        result = evaluate(allow(selector), allowed=["ws", "otp"])

        assert_result(result, FAIL, "otp_or_consumer_idp")


def test_require_login_method_interseca():
    result = evaluate(allow(sel("everyone"), require=[login("ws")]))

    assert_result(result, PASS)


def test_include_login_method_mas_email_admite_todo():
    result = evaluate(allow(login("ws"), sel("email")))

    assert_result(result, FAIL, "otp_or_consumer_idp")


def test_exclude_login_method_resta_el_metodo():
    result = evaluate(allow(sel("everyone"), exclude=[login("otp")]))

    assert_result(result, PASS)


def test_exclude_de_grupo_no_resta_nada():
    groups = [igroup("g", include=[login("otp")])]
    result = evaluate(allow(sel("everyone"), exclude=[sel("group", group="g")]), groups=groups)

    assert_result(result, FAIL, "otp_or_consumer_idp")


def test_exclude_de_selector_ligado_a_proveedor_distinto_de_login_method_no_resta():
    result = evaluate(allow(sel("everyone"), exclude=[sel("gsuite", idp="otp")]))

    assert_result(result, FAIL, "otp_or_consumer_idp")


def test_grupo_con_login_method_restringe():
    groups = [igroup("g", include=[login("ws")])]

    result = evaluate(allow(sel("group", group="g")), groups=groups)

    assert_result(result, PASS)


def test_grupos_mixtos_suman_sus_ramas():
    groups = [igroup("g1", include=[login("ws")]), igroup("g2", include=[sel("email")])]

    result = evaluate(allow(sel("group", group="g1"), sel("group", group="g2")), groups=groups)

    assert_result(result, FAIL, "otp_or_consumer_idp")


def test_grupo_anidado_resuelve_recursivamente():
    groups = [
        igroup("g1", include=[sel("group", group="g2")]),
        igroup("g2", include=[login("ws")]),
    ]

    assert_result(evaluate(allow(sel("group", group="g1")), groups=groups), PASS)


def test_ciclo_de_grupos_es_unknown():
    groups = [
        igroup("g1", include=[sel("group", group="g2")]),
        igroup("g2", include=[sel("group", group="g1")]),
    ]

    result = evaluate(allow(sel("group", group="g1")), groups=groups)

    assert_result(result, UNKNOWN, "idp_review", "ciclo")


@pytest.mark.parametrize(
    "selector",
    [
        sel("group", group="falta"),
        sel("group"),
        sel("unknown"),
        sel("azureAD"),
        sel("login_method"),
    ],
)
def test_referencias_ausentes_y_formas_desconocidas_son_unknown(selector):
    result = evaluate(allow(selector), allowed=["ws"])

    assert result.status is UNKNOWN
    assert result.reason_code == "idp_review"


def test_login_method_con_id_desconocido_es_unknown():
    assert_result(evaluate(allow(login("nope"))), UNKNOWN, "idp_review")


def test_varios_require_intersecan():
    result = evaluate(allow(sel("everyone"), require=[login("ws"), login("otp")]))

    assert_result(result, UNKNOWN, "idp_review", "no admiten ningún proveedor")


def test_varios_require_compatibles_dejan_el_directorio():
    result = evaluate(allow(sel("everyone"), require=[login("ws"), sel("email")]))

    assert_result(result, PASS)


def test_include_vacio_no_admite_a_nadie():
    assert_result(evaluate(allow()), UNKNOWN, "idp_review")


def test_service_auth_impide_pass():
    result = evaluate(allow(login("ws")), ipolicy("non_identity", [sel("service_token")]))

    assert_result(result, UNKNOWN, "non_identity", "token de servicio")


def test_bypass_es_fail():
    result = evaluate(allow(login("ws")), ipolicy("bypass", [sel("ip")]))

    assert_result(result, FAIL, "bypass")


def test_decision_desconocida_es_unknown():
    result = evaluate(allow(login("ws")), ipolicy("raro", [sel("email")]))

    assert_result(result, UNKNOWN, "idp_review", "decisión desconocida")


def test_deny_del_metodo_malo_baja_el_fail_a_unknown():
    result = evaluate(allow(sel("everyone")), ipolicy("deny", [login("otp")]))

    assert_result(result, UNKNOWN, "idp_review", "deny")


def test_deny_de_otro_metodo_no_baja_el_fail():
    result = evaluate(allow(sel("everyone")), ipolicy("deny", [login("ws")]))

    assert_result(result, FAIL, "otp_or_consumer_idp")


def test_deny_sin_login_method_no_baja_el_fail():
    result = evaluate(allow(sel("everyone")), ipolicy("deny", [sel("email_domain")]))

    assert_result(result, FAIL, "otp_or_consumer_idp")


def test_deny_no_convierte_en_pass():
    result = evaluate(allow(sel("everyone")), ipolicy("deny", [login("ws")]), idps=[WS])

    assert_result(result, PASS)


def test_allowed_idps_con_id_desconocido_es_unknown():
    assert_result(evaluate(allow(sel("everyone")), allowed=["nope"]), UNKNOWN, "idp_review")


def test_allowed_idps_malformado_es_unknown():
    assert_result(evaluate(allow(sel("everyone")), allowed="malformed"), UNKNOWN, "idp_review")


def test_evidencia_antigua_sin_campos_nuevos_es_unknown():
    old = iapp("a", policies=[allow(sel("everyone"))])
    del old["app"]["allowed_idps_form"]
    del old["app"]["allowed_idps"]

    result = only(run_identity(build_identity([old], ACCOUNT)))

    assert_result(result, UNKNOWN, "stale_evidence", "recoge de nuevo")


def test_politica_antigua_sin_selectores_es_unknown():
    old = {"decision": "allow", "include": ["everyone"], "require": []}

    assert_result(evaluate(old), UNKNOWN, "stale_evidence", "recoge de nuevo")


def test_evidencia_antigua_sin_seccion_de_proveedores_es_unknown():
    result = build_identity([iapp("a", policies=[allow(sel("everyone"))])], ACCOUNT)
    del result.data["sections"]["identity_providers"]

    assert_result(only(run_identity(result)), UNKNOWN, "stale_evidence")


@pytest.mark.parametrize("other", [SAML, idp("oi", "oidc"), idp("gh", "github")])
def test_saml_oidc_y_otros_piden_revision(other):
    result = evaluate(allow(login(other["id"])), idps=[other, WS])

    assert_result(result, UNKNOWN, "idp_review", other["type"])


def test_mas_de_un_directorio_pide_revision():
    result = evaluate(allow(sel("everyone")), idps=[WS, AZ])

    assert_result(result, UNKNOWN, "idp_review", "Workspace pyme", "Entra pyme")


def test_directorio_mas_saml_pide_revision():
    assert_result(evaluate(allow(sel("everyone")), idps=[WS, SAML]), UNKNOWN, "idp_review")


def test_un_solo_directorio_azure_es_pass():
    result = evaluate(allow(login("az")), idps=[AZ, OTP])

    assert_result(result, PASS, None, "Entra pyme (azureAD)")


def test_otp_gana_a_revision_de_otros_tipos():
    assert_result(evaluate(allow(sel("everyone")), idps=[OTP, SAML]), FAIL, "otp_or_consumer_idp")


def test_sin_politica_allow_es_unknown():
    assert_result(evaluate(ipolicy("deny", [login("otp")])), UNKNOWN, "idp_review")


def test_politicas_no_recogidas_son_unknown_traducido():
    app = iapp("a", policy_status="PERMISSION_DENIED", policies=())
    result = only(run_identity(build_identity([app], ACCOUNT)))

    assert (result.status, result.reason_code) == (UNKNOWN, "permission_denied")


def test_destino_no_interpretable_nunca_es_pass():
    app = iapp("a", "app.z.*", policies=[allow(login("ws"))])

    result = only(run_identity(build_identity([app], [WS])))

    assert_result(result, UNKNOWN, "scope_incomplete")


# Precondiciones globales


def test_403_en_proveedores_es_unknown_con_el_permiso_que_falta():
    section_403 = section(status="PERMISSION_DENIED", reason="sin permiso")
    result = build_identity(
        [iapp("a", policies=[allow(login("ws"))])], [], identity_section=section_403
    )

    check = only(run_identity(result))

    assert_result(check, UNKNOWN, "permission_denied", "Access: Organizations, Identity Providers")
    assert check.subject is None


def test_error_al_leer_proveedores_no_atribuye_el_permiso():
    result = build_identity(
        [iapp("a", policies=[allow(login("ws"))])],
        [],
        identity_section=section(status="ERROR", reason="fallo"),
    )

    check = only(run_identity(result))

    assert (check.status, check.reason_code) == (ERROR, None)
    assert "fallo" in check.reason
    assert "Access: Organizations" not in check.reason


def test_proveedores_not_supported_es_un_resultado_valido():
    result = build_identity(
        [iapp("a", policies=[allow(login("ws"))])],
        [],
        identity_section=section(status="NOT_SUPPORTED", reason="no disponible"),
    )

    check = only(run_identity(result))

    assert_result(check, UNKNOWN, "not_supported")
    assert "Access: Organizations" not in check.reason


def test_sin_proveedores_de_identidad_es_unknown():
    result = only(run_identity(build_identity([iapp("a", policies=[allow(sel("email"))])], [])))

    assert_result(result, UNKNOWN, "no_identity_providers")
    assert result.subject is None


def test_recogida_fallida_es_un_unico_resultado_traducido():
    result = build_identity([], [], collection_status="PERMISSION_DENIED")

    check = only(run_identity(result))

    assert (check.status, check.reason_code) == (UNKNOWN, "permission_denied")


def test_aplicaciones_no_recogidas_no_son_pass():
    result = build_identity([], [WS], applications=section(status="ERROR", reason="x"))

    assert only(run_identity(result)).status is not PASS


# Población compartida con tunnel_requires_access


def test_sin_apps_en_el_alcance_no_hay_resultados():
    app = iapp("a", "otro.example", policies=[allow(sel("everyone"))])

    assert run_identity(build_identity([app], ACCOUNT)) == []


def test_sin_tuneles_no_hay_resultados():
    assert run_identity(build_identity([iapp("a")], ACCOUNT, tunnels=[])) == []


def test_ruta_declarada_publica_queda_fuera_del_alcance():
    app = iapp("a", HOST, policies=[allow(sel("everyone"))])
    result = build_identity([app], ACCOUNT, public_routes=[declared(HOST)])

    assert run_identity(result) == []


def test_app_que_solo_cubre_el_subconjunto_publico_queda_fuera():
    app = iapp("a", f"{HOST}/agent/*", policies=[allow(sel("everyone"))])
    result = build_identity([app], ACCOUNT, public_routes=[declared(HOST, "/agent/*")])

    assert run_identity(result) == []


def test_app_que_solapa_con_un_path_de_la_ruta_esta_en_el_alcance():
    app = iapp("a", f"{HOST}/admin", policies=[allow(sel("everyone"))])
    tunnels = [remote(rule(HOST, "^/admin"))]

    result = only(run_identity(build_identity([app], ACCOUNT, tunnels=tunnels)))

    assert result.status is FAIL


def test_app_con_path_que_no_solapa_queda_fuera():
    app = iapp("a", f"{HOST}/otra", policies=[allow(sel("everyone"))])
    tunnels = [remote(rule(HOST, "^/admin"))]

    assert run_identity(build_identity([app], ACCOUNT, tunnels=tunnels)) == []


def test_bookmark_se_ignora():
    app = iapp("a", type="bookmark", policies=[allow(sel("everyone"))])

    assert run_identity(build_identity([app], ACCOUNT)) == []


def test_regla_sin_hostname_deja_un_unknown_de_alcance_y_evalua_las_apps_seguras():
    tunnels = [remote(rule(HOST), rule(None))]
    app = iapp("a", policies=[allow(sel("everyone"))])

    results = run_identity(build_identity([app], ACCOUNT, tunnels=tunnels))

    scope = [r for r in results if r.subject is None]
    assert [(r.status, r.reason_code) for r in scope] == [(UNKNOWN, "scope_incomplete")]
    assert [r.status for r in results if r.subject == "a"] == [FAIL]


def test_ingress_no_recogido_deja_un_unknown_de_alcance():
    tunnels = [remote(ingress_status="ERROR", reason="fallo")]

    results = run_identity(build_identity([iapp("a")], ACCOUNT, tunnels=tunnels))

    assert [(r.status, r.reason_code, r.subject) for r in results] == [
        (UNKNOWN, "scope_incomplete", None)
    ]


def test_tuneles_no_recogidos_dejan_un_unknown_de_alcance():
    result = build_identity(
        [iapp("a")], ACCOUNT, tunnels_section=section(status="PERMISSION_DENIED", reason="403")
    )

    assert [(r.status, r.reason_code) for r in run_identity(result)] == [
        (UNKNOWN, "scope_incomplete")
    ]


def test_tunel_local_con_dns_incompleto_deja_un_unknown_de_alcance():
    tunnels = [local(HOST, complete=False)]
    app = iapp("a", policies=[allow(login("ws"))])

    results = run_identity(build_identity([app], [WS], tunnels=tunnels))

    assert (UNKNOWN, "scope_incomplete", None) in [
        (r.status, r.reason_code, r.subject) for r in results
    ]
    assert [r.status for r in results if r.subject == "a"] == [PASS]


def test_tunnel_requires_access_no_cambia_con_la_poblacion_extraida():
    result = build_identity([iapp("a")], ACCOUNT)

    assert only(run(result)).subject == f"t-1 {HOST}/*"


def test_proveedor_sin_tipo_se_etiqueta_sin_parentesis_vacios():
    typeless = {"id": "ws", "name": "Sin tipo"}
    check = evaluate(allow(sel("everyone")), idps=[typeless, OTP])

    assert_result(check, FAIL, "otp_or_consumer_idp")
    assert "None" not in check.reason
