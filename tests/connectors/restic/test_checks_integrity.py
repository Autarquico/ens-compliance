"""BACKUP-004: último `backup check` sin errores, reciente y con cobertura suficiente."""

import pytest

from ens_evidence.connectors.restic.checks import backup_004_integrity as check
from ens_evidence.models.enums import CheckStatus

from .check_data import DAY, HOUR, check_item, evidence, run, section

PASS, FAIL, UNKNOWN, ERROR = (
    CheckStatus.PASS,
    CheckStatus.FAIL,
    CheckStatus.UNKNOWN,
    CheckStatus.ERROR,
)


def checks(*items, unreadable=()):
    return section(items, unreadable=list(unreadable))


def status(*items, **kwargs) -> CheckStatus:
    return run(check, evidence(integrity_checks=checks(*items), **kwargs)).status


def test_un_check_reciente_sin_errores_y_con_cobertura_suficiente_pasa_y_muestra_la_cobertura():
    result = run(check, evidence(integrity_checks=checks(check_item(coverage=10))))

    assert result.status is PASS
    assert "10 %" in result.reason
    assert "5 %" in result.reason


@pytest.mark.parametrize(
    ("coverage", "expected"), [(4, FAIL), (5, PASS), (6, PASS), (100, PASS), (0, FAIL)]
)
def test_la_cobertura_debe_alcanzar_el_minimo_exigido(coverage, expected):
    assert status(check_item(coverage=coverage)) is expected


def test_solo_metadatos_pasa_si_la_organizacion_no_exige_lectura_de_datos():
    result = run(
        check,
        evidence(
            integrity_checks=checks(check_item(coverage=0)),
            thresholds={"min_check_read_data_percent": 0},
        ),
    )

    assert result.status is PASS


@pytest.mark.parametrize(
    ("age", "expected"),
    [(35 * DAY, PASS), (35 * DAY + HOUR, FAIL), (60 * DAY, FAIL), (HOUR, PASS)],
)
def test_el_check_debe_ser_mas_reciente_que_el_maximo_de_dias(age, expected):
    assert status(check_item(age=age)) is expected


def test_un_check_con_errores_falla():
    result = run(check, evidence(integrity_checks=checks(check_item("c1", "failed"))))

    assert result.status is FAIL
    assert "falló" in result.reason


def test_un_check_que_quedo_sin_terminar_falla():
    assert status(check_item("c1", "started")) is FAIL


def test_un_intento_posterior_fallido_manda_sobre_un_exito_anterior():
    assert status(check_item("c1", age=10 * DAY), check_item("c2", "failed", age=DAY)) is FAIL


def test_un_intento_posterior_interrumpido_manda_sobre_un_exito_anterior():
    assert status(check_item("c1", age=10 * DAY), check_item("c2", "started", age=DAY)) is FAIL


def test_un_exito_posterior_a_un_fallo_pasa():
    assert status(check_item("c1", "failed", age=10 * DAY), check_item("c2", age=DAY)) is PASS


def test_nunca_ejecutado_es_unknown():
    assert status() is UNKNOWN


def test_si_el_ultimo_intento_es_de_otro_repositorio_es_unknown():
    assert status(check_item("c1", age=10 * DAY), check_item("c2", age=DAY, bound=False)) is UNKNOWN


def test_un_registro_ilegible_es_unknown():
    result = run(check, evidence(integrity_checks=checks(check_item(), unreadable=["x"])))

    assert result.status is UNKNOWN


def test_un_check_que_termina_en_el_futuro_es_unknown():
    assert status(check_item(age=-HOUR)) is UNKNOWN


def test_un_check_completado_sin_fin_interpretable_es_unknown():
    assert status(check_item(finished_at=None)) is UNKNOWN


@pytest.mark.parametrize("value", [None, 0, -1, "35", True])
def test_un_umbral_de_edad_ausente_o_invalido_da_unknown(value):
    assert status(check_item(), thresholds={"max_check_age_days": value}) is UNKNOWN


@pytest.mark.parametrize("value", [None, -1, 101, "5", True])
def test_una_cobertura_minima_ausente_o_invalida_da_unknown(value):
    assert status(check_item(), thresholds={"min_check_read_data_percent": value}) is UNKNOWN


def test_la_recogida_global_fallida_no_da_pass():
    assert status(check_item(), collection_status="ERROR", error="x") is ERROR
