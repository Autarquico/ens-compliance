"""BACKUP-001: último snapshot del repositorio, citado por una ejecución completada, reciente."""

from datetime import timedelta

import pytest

from ens_evidence.connectors.restic.checks import backup_001_recent as check
from ens_evidence.models.enums import CheckStatus

from .check_data import HOUR, evidence, run, run_item, section, snapshot, snapshots
from .fake_restic import REPOSITORY_ID

PASS, FAIL, UNKNOWN, ERROR = (
    CheckStatus.PASS,
    CheckStatus.FAIL,
    CheckStatus.UNKNOWN,
    CheckStatus.ERROR,
)


def status(**sections) -> CheckStatus:
    return run(check, evidence(**sections)).status


def test_un_snapshot_reciente_citado_por_una_ejecucion_completada_pasa():
    result = run(check, evidence())

    assert result.status is PASS
    assert result.subject == REPOSITORY_ID


@pytest.mark.parametrize(
    ("age", "expected"),
    [
        (timedelta(0), PASS),
        (26 * HOUR - timedelta(seconds=1), PASS),
        (26 * HOUR, PASS),
        (26 * HOUR + timedelta(seconds=1), FAIL),
        (30 * 24 * HOUR, FAIL),
        (-timedelta(seconds=1), UNKNOWN),
        (-5 * HOUR, UNKNOWN),
    ],
    ids=[
        "edad-0",
        "justo-antes",
        "en-el-umbral",
        "pasado-un-segundo",
        "viejo",
        "futuro-1s",
        "futuro-5h",
    ],
)
def test_la_edad_debe_cumplir_0_menor_o_igual_que_edad_menor_o_igual_que_el_umbral(age, expected):
    assert status(snapshots=snapshots(snapshot(age))) is expected


def test_la_edad_se_mide_contra_el_snapshot_mas_reciente_y_no_el_primero_de_la_lista():
    old, new = snapshot(40 * HOUR, snapshot_id="9f" * 32, run_id="r0"), snapshot(HOUR)
    runs = section([run_item("r0"), run_item("r1")], unreadable=[])

    assert status(snapshots=snapshots(old, new), runs=runs) is PASS


def test_sin_snapshots_en_un_repositorio_accesible_falla():
    assert status(snapshots=snapshots()) is FAIL


def test_un_repositorio_inexistente_falla():
    assert status(snapshots=snapshots(exists=False)) is FAIL


def test_un_snapshot_ajeno_no_cuenta_y_deja_el_resultado_en_unknown():
    assert status(snapshots=snapshots(snapshot(run_id=None, foreign=True))) is UNKNOWN


def test_un_snapshot_ajeno_reciente_no_tapa_a_uno_propio_viejo():
    foreign = snapshot(HOUR, run_id=None, foreign=True, snapshot_id="9f" * 32)

    assert status(snapshots=snapshots(snapshot(48 * HOUR), foreign)) is FAIL


@pytest.mark.parametrize("other_status", ["failed", "started"])
def test_un_snapshot_citado_por_una_ejecucion_no_completada_no_cuenta(other_status):
    runs = section([run_item("r1", other_status)], unreadable=[])

    assert status(runs=runs) is UNKNOWN


def test_un_snapshot_citado_por_una_ejecucion_de_otro_repositorio_no_pasa():
    runs = section([run_item("r1", bound=False)], unreadable=[])

    assert status(runs=runs) is not PASS


def test_un_registro_ilegible_impide_el_pass():
    assert status(runs=section([run_item()], unreadable=["evidence/backups/rota.yaml"])) is UNKNOWN


@pytest.mark.parametrize("name", ["snapshots", "runs"])
def test_una_seccion_no_recogida_no_da_pass(name):
    broken = section(status="ERROR", reason="falló")

    assert status(**{name: broken}) is ERROR


def test_una_seccion_sin_permiso_es_unknown():
    assert status(snapshots=section(status="PERMISSION_DENIED", reason="denegado")) is UNKNOWN


@pytest.mark.parametrize("value", [None, 0, -1, "26", True])
def test_un_umbral_invalido_en_la_evidencia_da_unknown(value):
    assert run(check, evidence(thresholds={"max_backup_age_hours": value})).status is UNKNOWN


def test_la_fecha_ilegible_de_un_snapshot_da_unknown():
    broken = {**snapshot(), "time": "ayer"}

    assert status(snapshots=snapshots(broken)) is UNKNOWN
