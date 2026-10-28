import pytest

from ens_evidence.models.enums import CheckStatus

from .check_data import app, build, group, only, policy, remote, rule, run, section

PASS, UNKNOWN = CheckStatus.PASS, CheckStatus.UNKNOWN
HOST = "app.z.example"


def evaluate(groups, *policies, groups_section=None):
    result = build(
        [remote(rule(HOST))],
        [app("a", HOST, policies=policies)],
        groups_section=groups_section or section(groups),
    )
    return only(run(result))


def via_group(group_id="g1"):
    return policy("allow", ["group"], include_groups=[group_id])


def test_grupo_solo_ip_no_demuestra_identidad():
    result = evaluate([group("g1", include=["ip"])], via_group())

    assert result.status is UNKNOWN


def test_grupo_de_service_token_no_demuestra_identidad():
    result = evaluate([group("g1", include=["service_token"])], via_group())

    assert result.status is UNKNOWN


def test_grupo_de_emails_es_identidad():
    assert evaluate([group("g1", include=["email"])], via_group()).status is PASS


def test_grupo_con_require_de_identidad_es_identidad():
    result = evaluate([group("g1", include=["ip"], require=["email"])], via_group())

    assert result.status is PASS


def test_grupo_anidado_de_identidad_es_identidad():
    groups = [
        group("g1", include=["group"], include_groups=["g2"]),
        group("g2", include=["email_domain"]),
    ]

    assert evaluate(groups, via_group()).status is PASS


def test_grupo_anidado_solo_ip_no_es_identidad():
    groups = [group("g1", include=["group"], include_groups=["g2"]), group("g2", include=["ip"])]

    assert evaluate(groups, via_group()).status is UNKNOWN


def test_un_grupo_que_no_es_identidad_junto_a_un_email_en_include_no_basta():
    groups = [group("g1", include=["ip"])]

    result = evaluate(groups, policy("allow", ["email", "group"], include_groups=["g1"]))

    assert result.status is UNKNOWN


def test_grupo_en_require_de_identidad_con_include_de_ip_es_identidad():
    groups = [group("g1", include=["email"])]

    result = evaluate(groups, policy("allow", ["ip"], ["group"], require_groups=["g1"]))

    assert result.status is PASS


def test_ciclo_entre_grupos_es_unknown():
    groups = [
        group("g1", include=["group"], include_groups=["g2"]),
        group("g2", include=["group"], include_groups=["g1"]),
    ]

    result = evaluate(groups, via_group())

    assert result.status is UNKNOWN
    assert "ciclo" in result.reason


def test_grupo_que_se_referencia_a_si_mismo_es_unknown():
    result = evaluate([group("g1", include=["group"], include_groups=["g1"])], via_group())

    assert result.status is UNKNOWN


def test_grupo_inexistente_es_unknown():
    result = evaluate([group("otro")], via_group("g1"))

    assert result.status is UNKNOWN
    assert "no consta" in result.reason


def test_selector_group_sin_id_es_unknown():
    result = evaluate([group("g1")], policy("allow", ["group"]))

    assert result.status is UNKNOWN


@pytest.mark.parametrize("status", ["PERMISSION_DENIED", "ERROR", "NOT_SUPPORTED"])
def test_seccion_de_grupos_no_recogida_no_es_pass(status):
    result = evaluate([], via_group(), groups_section=section(status=status, reason="sin permiso"))

    assert result.status is not PASS
    assert result.collection_status.value == status


def test_seccion_de_grupos_ausente_no_es_pass():
    result = build([remote(rule(HOST))], [app("a", HOST, policies=[via_group()])])
    del result.data["sections"]["access_groups"]

    assert only(run(result)).status is not PASS


def test_grupos_no_recogidos_no_afectan_a_politicas_sin_grupos():
    result = evaluate([], policy("allow", ["email"]), groups_section=section(status="ERROR"))

    assert result.status is PASS
