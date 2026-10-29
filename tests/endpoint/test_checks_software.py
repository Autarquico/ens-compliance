"""HOST-007: inventario de software."""

from ens_evidence.models.enums import CheckStatus

from .check_data import evaluate, host_evidence

INSTALLED = "install ok installed"


def mac(*apps):
    return evaluate(host_evidence(rows={"apps": list(apps)}))["HOST-007"]


def deb(*packages, rpm=()):
    rows = {"deb_packages": list(packages), "rpm_packages": list(rpm)}
    return evaluate(host_evidence("linux", rows=rows))["HOST-007"]


def test_filas_validas_en_macos_pasan_con_recuento_y_aviso_de_cobertura():
    result = mac(
        {"name": "Safari.app", "bundle_short_version": "18"},
        {"name": "Notes.app", "bundle_version": "4"},
    )

    assert result.status is CheckStatus.PASS
    assert "2 elementos" in result.reason
    assert "no todo el software" in result.reason


def test_apps_sin_nombre_o_sin_version_no_cuentan():
    result = mac({"name": "", "bundle_short_version": "1"}, {"name": "X.app"})

    assert result.status is CheckStatus.UNKNOWN


def test_deb_instalados_pasan_con_recuento_sin_el_aviso_de_apps():
    result = deb(
        {"name": "openssl", "version": "3.0", "status": INSTALLED},
        {"name": "curl", "version": "8.0", "status": INSTALLED},
    )

    assert result.status is CheckStatus.PASS
    assert "2 elementos" in result.reason
    assert "apps" not in result.reason


def test_los_deb_no_instalados_no_cuentan():
    result = deb(
        {"name": "openssl", "version": "3.0", "status": INSTALLED},
        {"name": "viejo", "version": "1.0", "status": "deinstall ok config-files"},
    )

    assert "1 elementos" in result.reason


def test_solo_deb_no_instalados_es_unknown():
    result = deb({"name": "viejo", "version": "1.0", "status": "deinstall ok config-files"})

    assert result.status is CheckStatus.UNKNOWN


def test_los_rpm_cuentan_en_linux():
    result = deb(rpm=[{"name": "bash", "version": "5.1"}])

    assert result.status is CheckStatus.PASS


def test_sin_filas_es_unknown():
    assert mac().status is CheckStatus.UNKNOWN
    assert deb().status is CheckStatus.UNKNOWN
