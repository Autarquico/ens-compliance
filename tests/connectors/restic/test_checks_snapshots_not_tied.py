"""BACKUP-001: snapshots que existen pero no se pueden atar a un backup propio no son un FAIL."""

from ens_evidence.connectors.restic.checks import backup_001_recent as check
from ens_evidence.models.enums import CheckStatus

from .check_data import HOUR, evidence, run, snapshot, snapshots


def test_con_snapshots_pero_ninguno_atado_el_resultado_es_desconocido():
    collected = evidence(snapshots=snapshots(snapshot(run_id=None, foreign=True)))

    result = run(check, collected)

    assert result.status is CheckStatus.UNKNOWN
    assert "foreign_snapshots" in result.reason


def test_un_repositorio_accesible_sin_ningun_snapshot_falla():
    result = run(check, evidence(snapshots=snapshots()))

    assert result.status is CheckStatus.FAIL


def test_el_ultimo_snapshot_atado_caducado_falla_aunque_haya_otro_ajeno_reciente():
    old = snapshot(age=40 * HOUR)
    recent_foreign = snapshot(age=HOUR, run_id=None, foreign=True, snapshot_id="9f" * 32)

    result = run(check, evidence(snapshots=snapshots(old, recent_foreign)))

    assert result.status is CheckStatus.FAIL
