"""HOST-003: sistema operativo soportado por política."""

from datetime import date

import pytest

from ens_evidence.endpoint.config import SupportedOs
from ens_evidence.models.enums import CheckStatus

from .check_data import AT, config, evaluate, host_evidence

PASS, FAIL, UNKNOWN = CheckStatus.PASS, CheckStatus.FAIL, CheckStatus.UNKNOWN


def host003(os_row, policies, platform="darwin"):
    evidence = host_evidence(platform, rows={"os_version": [os_row]})
    return evaluate(evidence, endpoint_config=config(policies))["HOST-003"]


def mac(version):
    return {"name": "macOS", "platform": "darwin", "version": version}


def macos_policy(release, min_version=None, end_of_support=None):
    return SupportedOs(
        platform="darwin",
        name="macOS",
        release=release,
        min_version=min_version,
        end_of_support=end_of_support,
    )


@pytest.mark.parametrize(
    ("version", "minimum", "status"),
    [
        ("15.1", "15", PASS),
        ("15", "15", PASS),
        ("15.0.0", "15", PASS),
        ("15.2", "15.3", FAIL),
        ("15.0", "15.3", FAIL),
        ("15.3", "15.3", PASS),
        ("15.2", "15.10", FAIL),
        ("15.10", "15.2", PASS),
    ],
)
def test_la_version_se_compara_numericamente_con_el_minimo_de_su_release(version, minimum, status):
    assert host003(mac(version), [macos_policy("15", minimum)]).status is status


def test_una_release_sin_version_minima_basta_con_estar_en_la_release():
    assert host003(mac("15.0"), [macos_policy("15")]).status is PASS


def test_pass_cita_la_version_y_el_minimo():
    result = host003(mac("15.1"), [macos_policy("15")])

    assert "15.1" in result.reason


def test_el_fin_de_soporte_por_fecha_falla_aunque_la_version_alcance_el_minimo():
    policy = macos_policy("15", end_of_support=AT.date())

    assert host003(mac("15.1"), [policy]).status is FAIL


def test_antes_del_fin_de_soporte_pasa():
    policy = macos_policy("15", end_of_support=date(2026, 10, 16))

    assert host003(mac("15.1"), [policy]).status is PASS


def test_sin_politica_para_la_plataforma_o_el_nombre_es_unknown():
    for policies in ([], [SupportedOs(platform="linux", name="macOS", release="15")]):
        result = host003(mac("15.1"), policies)
        assert result.status is UNKNOWN
        assert result.reason_code == "no_policy"
    other_name = [SupportedOs(platform="darwin", name="Mac OS X", release="10")]
    assert host003(mac("15.1"), other_name).status is UNKNOWN


def test_el_nombre_del_sistema_no_distingue_mayusculas():
    policy = SupportedOs(platform="darwin", name="MACOS", release="15")

    assert host003(mac("15.1"), [policy]).status is PASS


@pytest.mark.parametrize("row", [{"name": "macOS", "version": ""}, {"name": "", "version": "15"}])
def test_una_version_o_nombre_no_interpretables_son_unknown(row):
    assert host003(row, [macos_policy("15")]).status is UNKNOWN


def test_dos_filas_de_os_version_son_unknown():
    evidence = host_evidence(rows={"os_version": [mac("15.1"), mac("9")]})

    assert evaluate(evidence)["HOST-003"].status is UNKNOWN


def debian(version):
    return {"name": "Debian GNU/Linux", "platform": "debian", "version": version}


def debian_policy(release, min_version=None):
    return SupportedOs(
        platform="linux", name="Debian GNU/Linux", release=release, min_version=min_version
    )


def test_en_linux_la_politica_es_la_de_la_release_mayor_de_la_distribucion():
    policies = [debian_policy("11"), debian_policy("12", "12.2")]

    assert host003(debian("12.5"), policies, "linux").status is PASS
    assert host003(debian("12.1"), policies, "linux").status is FAIL
    assert host003(debian("11.9"), policies, "linux").status is PASS


def test_en_linux_una_release_sin_politica_es_unknown():
    result = host003(debian("10.1"), [debian_policy("12")], "linux")

    assert result.status is UNKNOWN


def test_macos_sin_politica_para_su_release_es_unknown_aunque_otra_anterior_exista():
    result = host003(mac("15.0"), [macos_policy("14", "14.7")])

    assert result.status is UNKNOWN
    assert result.reason_code == "no_policy"


def test_macos_26_sin_politica_es_unknown_y_no_hereda_la_de_la_15():
    result = host003(mac("26.0"), [macos_policy("15", "15.3")])

    assert result.status is UNKNOWN
    assert result.reason_code == "no_policy"


def test_macos_cada_release_se_evalua_con_su_propia_politica():
    policies = [macos_policy("14", "14.7"), macos_policy("15", "15.3")]

    assert host003(mac("15.0"), policies).status is FAIL
    assert host003(mac("15.3"), policies).status is PASS
    assert host003(mac("14.7"), policies).status is PASS


def test_el_fin_de_soporte_de_otra_release_no_afecta_a_la_del_equipo():
    policies = [macos_policy("14", end_of_support=date(2026, 9, 1)), macos_policy("15", "15.3")]

    assert host003(mac("15.3"), policies).status is PASS
    assert host003(mac("14.8"), policies).status is FAIL


def test_el_fin_de_soporte_de_la_release_falla_aunque_la_version_alcance_el_minimo():
    policy = macos_policy("15", "15.3", end_of_support=date(2026, 10, 2))

    assert host003(mac("15.4"), [policy]).status is FAIL


def ubuntu(version):
    return {"name": "Ubuntu", "platform": "ubuntu", "version": version}


def ubuntu_policy(release, min_version=None, end_of_support=None):
    return SupportedOs(
        platform="linux",
        name="Ubuntu",
        release=release,
        min_version=min_version,
        end_of_support=end_of_support,
    )


def test_ubuntu_interina_no_cumple_la_politica_de_otra_release_de_la_misma_mayor():
    policy = ubuntu_policy("22.04", end_of_support=date(2027, 4, 30))

    result = host003(ubuntu("22.10 (Kinetic Kudu)"), [policy], "linux")

    assert result.status is UNKNOWN
    assert result.reason_code == "no_policy"


def test_ubuntu_lts_se_evalua_con_la_politica_de_su_release():
    policies = [ubuntu_policy("20.04"), ubuntu_policy("22.04")]

    assert host003(ubuntu("22.04.5 LTS"), policies, "linux").status is PASS
    assert host003(ubuntu("22.04"), [ubuntu_policy("22.04", "22.04.3")], "linux").status is FAIL


def test_una_version_con_menos_detalle_que_la_minima_no_se_da_por_anterior():
    debian = {"name": "Debian GNU/Linux", "platform": "debian", "version": "12 (bookworm)"}
    policy = SupportedOs(
        platform="linux", name="Debian GNU/Linux", release="12", min_version="12.5"
    )

    result = host003(debian, [policy], platform="linux")

    assert result.status is UNKNOWN
    assert "12.5" in result.reason
