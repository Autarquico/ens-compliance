"""BACKUP-005: un restore test caducado falla aunque su restauración ya no esté vinculada."""

from ens_evidence.connectors.restic.checks import backup_005_restore_test as check
from ens_evidence.models.enums import CheckStatus

from .check_data import DAY, evidence, restore_test_item, run, section


def test_caducado_por_vigencia_sin_restauracion_vinculada_falla():
    expired = restore_test_item(age=200 * DAY, linked=False)

    result = run(check, evidence(restore_tests=section([expired], unreadable=[])))

    assert result.status is CheckStatus.FAIL


def test_con_vigencia_pero_sin_restauracion_vinculada_sigue_siendo_unknown():
    unlinked = restore_test_item(linked=False)

    result = run(check, evidence(restore_tests=section([unlinked], unreadable=[])))

    assert result.status is CheckStatus.UNKNOWN
