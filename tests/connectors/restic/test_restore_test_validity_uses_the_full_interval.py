"""La vigencia compara el intervalo completo, no los días enteros truncados."""

from datetime import timedelta

import pytest

from ens_evidence.connectors.restic.checks import backup_005_restore_test as check
from ens_evidence.models.enums import CheckStatus

from .check_data import DAY, HOUR, evidence, restore_test_item, run, section

VALIDITY_DAYS = 90


@pytest.mark.parametrize(
    ("age", "expected"),
    [
        (VALIDITY_DAYS * DAY, CheckStatus.PASS),
        (VALIDITY_DAYS * DAY + timedelta(seconds=1), CheckStatus.FAIL),
        (VALIDITY_DAYS * DAY + 23 * HOUR, CheckStatus.FAIL),
    ],
    ids=["en-el-umbral", "un-segundo-despues", "noventa-dias-y-23-horas"],
)
def test_un_restore_test_mas_viejo_que_la_vigencia_falla_aunque_los_dias_enteros_coincidan(
    age, expected
):
    tests = section([restore_test_item(age=age)], unreadable=[])

    assert run(check, evidence(restore_tests=tests)).status is expected
