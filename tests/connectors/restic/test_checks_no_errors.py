"""BACKUP-002: la última ejecución registrada (no el último éxito) está completada."""

from datetime import timedelta

import pytest

from ens_evidence.connectors.restic.checks import backup_002_no_errors as check
from ens_evidence.models.enums import CheckStatus

from .check_data import HOUR, evidence, run, run_item, section, snapshot, snapshots

PASS, FAIL, UNKNOWN, ERROR = (
    CheckStatus.PASS,
    CheckStatus.FAIL,
    CheckStatus.UNKNOWN,
    CheckStatus.ERROR,
)


def runs(*items, unreadable=()):
    return section(items, unreadable=list(unreadable))


def status(*items, **sections) -> CheckStatus:
    return run(check, evidence(runs=runs(*items), **sections)).status


def test_una_ejecucion_completada_con_su_snapshot_en_el_repositorio_pasa():
    assert status(run_item()) is PASS


def test_una_ejecucion_fallida_da_fail_y_dice_que_fallo():
    result = run(check, evidence(runs=runs(run_item("r1", "failed"))))

    assert result.status is FAIL
    assert "falló" in result.reason


def test_una_ejecucion_que_quedo_sin_terminar_da_fail():
    result = run(check, evidence(runs=runs(run_item("r1", "started"))))

    assert result.status is FAIL
    assert "interrumpida" in result.reason


def test_el_ultimo_intento_fallido_manda_sobre_un_exito_anterior():
    items = (run_item("r1", age=30 * HOUR), run_item("r2", "failed", age=2 * HOUR))

    assert status(*items) is FAIL


def test_el_ultimo_intento_interrumpido_manda_sobre_un_exito_anterior():
    items = (run_item("r1", age=30 * HOUR), run_item("r2", "started", age=2 * HOUR))

    assert status(*items) is FAIL


def test_un_exito_posterior_a_un_fallo_pasa():
    items = (run_item("r1", "failed", age=30 * HOUR), run_item("r2", age=2 * HOUR))

    assert status(*items, snapshots=snapshots(snapshot(run_id="r2"))) is PASS


def test_el_ultimo_intento_se_elige_por_fecha_y_no_por_posicion_en_la_lista():
    items = (run_item("r2", "failed", age=2 * HOUR), run_item("r1", age=30 * HOUR))

    assert status(*items) is FAIL


def test_sin_ejecuciones_registradas_es_unknown():
    assert status() is UNKNOWN


def test_si_el_ultimo_intento_es_de_otro_repositorio_es_unknown_aunque_haya_un_exito_atado_antes():
    items = (run_item("r1", age=30 * HOUR), run_item("r2", age=2 * HOUR, bound=False))

    assert status(*items) is UNKNOWN


def test_una_ejecucion_completada_cuyo_snapshot_no_esta_en_el_repositorio_es_unknown():
    assert status(run_item(), snapshots=snapshots()) is UNKNOWN


def test_una_ejecucion_completada_cuyo_snapshot_es_ajeno_es_unknown():
    foreign = snapshot(run_id=None, foreign=True)

    assert status(run_item(), snapshots=snapshots(foreign)) is UNKNOWN


def test_un_registro_ilegible_podria_ser_el_ultimo_intento_y_da_unknown():
    result = run(check, evidence(runs=runs(run_item(), unreadable=["evidence/backups/x.yaml"])))

    assert result.status is UNKNOWN
    assert "ilegibles" in result.reason


def test_un_fallo_posterior_no_se_oculta_si_otro_registro_es_ilegible_y_el_ultimo_es_legible():
    items = (run_item("r1", "failed", age=2 * HOUR),)

    assert run(check, evidence(runs=runs(*items, unreadable=["x"]))).status is not PASS


@pytest.mark.parametrize(
    "changes",
    [
        {"started_at": "no-es-fecha"},
        {"finished_at": "no-es-fecha"},
        {"started_at": "2026-10-28T10:00:00"},
    ],
    ids=["inicio-ilegible", "fin-ilegible", "inicio-sin-zona"],
)
def test_fechas_ilegibles_en_la_ultima_ejecucion_dan_unknown(changes):
    assert status(run_item(**changes)) is UNKNOWN


def test_una_ejecucion_que_empieza_en_el_futuro_da_unknown():
    assert status(run_item(age=-timedelta(hours=1))) is UNKNOWN


def test_una_ejecucion_que_termina_antes_de_empezar_da_unknown():
    item = run_item(finished_at=run_item(age=3 * HOUR)["started_at"])

    assert status(item) is UNKNOWN


def test_un_estado_desconocido_da_unknown():
    assert status(run_item(status="raro")) is UNKNOWN


def test_la_ejecucion_reconstruida_pasa_pero_lo_dice_en_el_motivo():
    result = run(check, evidence(runs=runs(run_item(reconstructed_from_snapshot=True))))

    assert result.status is PASS
    assert "reconstruida" in result.reason


def test_una_seccion_de_ejecuciones_no_recogida_no_da_pass():
    assert run(check, evidence(runs=section(status="ERROR", reason="x"))).status is ERROR
