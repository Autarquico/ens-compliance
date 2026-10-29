"""BACKUP-005: último restore test completo, aprobado, atado a una restauración y vigente."""

from datetime import timedelta

import pytest

from ens_evidence.connectors.restic.checks import backup_005_restore_test as check
from ens_evidence.models.enums import CheckStatus

from .check_data import DAY, HOUR, evidence, restore_test_item, run, section

PASS, FAIL, UNKNOWN, ERROR = (
    CheckStatus.PASS,
    CheckStatus.FAIL,
    CheckStatus.UNKNOWN,
    CheckStatus.ERROR,
)


def declared(*items, unreadable=()):
    return section(items, unreadable=list(unreadable))


def status(*items, **kwargs) -> CheckStatus:
    return run(check, evidence(restore_tests=declared(*items), **kwargs)).status


def test_un_restore_test_completo_aprobado_atado_y_vigente_pasa_y_aclara_el_destino_aislado():
    result = run(check, evidence())

    assert result.status is PASS
    assert result.subject == "t1"
    assert "declaración humana" in result.reason


@pytest.mark.parametrize(
    "result",
    [
        {"restored": False},
        {"integrity_verified": False},
        {"application_started": False},
        {"application_started": "not_applicable"},
        {"restored": "true"},
    ],
    ids=[
        "no-restaurado",
        "integridad-no-verificada",
        "aplicacion-no-arranco",
        "no-aplicable-sin-justificar",
        "valor-no-booleano",
    ],
)
def test_un_resultado_declarado_negativo_o_sin_justificar_falla(result):
    assert status(restore_test_item(result=result)) is FAIL


def test_la_aplicacion_no_aplicable_con_justificacion_pasa():
    result = {"application_started": "not_applicable", "application_justification": "Solo ficheros"}

    assert status(restore_test_item(result=result)) is PASS


def test_un_destino_no_declarado_aislado_falla():
    assert status(restore_test_item(isolated_target=False)) is FAIL


def test_una_revision_no_aprobada_falla():
    assert status(restore_test_item(review={"approved": False})) is FAIL


@pytest.mark.parametrize(
    ("age", "expected"),
    [
        (HOUR * 24 * 90, PASS),
        (DAY * 90 + HOUR, FAIL),
        (DAY * 91, FAIL),
        (DAY * 120, FAIL),
        (DAY, PASS),
    ],
    ids=["en-el-umbral", "pasado-90d-y-1h", "pasado-91d", "caducado-120d", "reciente"],
)
def test_el_restore_test_debe_ser_mas_reciente_que_los_dias_de_vigencia(age, expected):
    assert status(restore_test_item(age=age)) is expected


def test_un_restore_test_fuera_de_vigencia_falla_y_lo_dice():
    result = run(check, evidence(restore_tests=declared(restore_test_item(age=120 * DAY))))

    assert result.status is FAIL
    assert "120 d" in result.reason
    assert "90 d" in result.reason


def test_una_revision_con_valid_until_pasado_falla():
    assert status(restore_test_item(age=10 * DAY, valid_for=DAY)) is FAIL


def test_una_revision_que_caduca_justo_en_la_evaluacion_ya_no_esta_vigente():
    assert status(restore_test_item(age=2 * HOUR, valid_for=timedelta(hours=1))) is FAIL


def test_el_ultimo_restore_test_manda_sobre_uno_anterior_que_pasaba():
    failing = restore_test_item("t2", age=DAY, result={"restored": False})

    result = run(
        check, evidence(restore_tests=declared(restore_test_item("t1", age=20 * DAY), failing))
    )

    assert result.status is FAIL
    assert result.subject == "t2"


def test_ninguno_registrado_es_unknown():
    assert status() is UNKNOWN


def test_un_restore_test_no_atado_a_una_restauracion_verificada_es_unknown():
    assert status(restore_test_item(linked=False)) is UNKNOWN


def test_un_restore_test_de_otro_repositorio_es_unknown():
    assert status(restore_test_item(bound=False)) is UNKNOWN


def test_un_registro_ilegible_es_unknown():
    result = run(check, evidence(restore_tests=declared(restore_test_item(), unreadable=["x"])))

    assert result.status is UNKNOWN


def test_una_revision_en_el_futuro_es_unknown():
    assert status(restore_test_item(age=-DAY)) is UNKNOWN


def test_una_revision_anterior_a_la_prueba_es_unknown():
    item = restore_test_item()
    item["review"]["reviewed_at"] = restore_test_item(age=20 * DAY)["review"]["reviewed_at"]

    assert status(item) is UNKNOWN


@pytest.mark.parametrize("field", ["reviewed_at", "valid_until"])
def test_una_fecha_ilegible_en_la_revision_es_unknown(field):
    assert status(restore_test_item(review={field: "pronto"})) is UNKNOWN


@pytest.mark.parametrize("value", [None, 0, -1, "90"])
def test_un_umbral_de_vigencia_invalido_da_unknown(value):
    assert status(restore_test_item(), thresholds={"restore_test_validity_days": value}) is UNKNOWN


def test_la_recogida_global_fallida_no_da_pass():
    assert status(restore_test_item(), collection_status="ERROR", error="x") is ERROR
