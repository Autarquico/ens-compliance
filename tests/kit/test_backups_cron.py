"""La periodicidad del kit de copias: solo crones con cinco campos dentro de su rango."""

import pytest
from pydantic import ValidationError

from ens_evidence.kit.backups import Schedule

DAYS = {
    "retention_days": 90,
    "max_backup_age_hours": 26,
    "max_check_age_days": 35,
    "restore_test_validity_days": 365,
}


def schedule(cron: str) -> Schedule:
    return Schedule(cron=cron, **DAYS)


@pytest.mark.parametrize(
    "cron",
    ["0 2 * * *", "*/15 0-6 1,15 * 0", "59 23 31 12 6", "5/10 * * * *", "0 2  * * *"],
)
def test_un_cron_dentro_de_rango_se_acepta_y_se_normaliza(cron):
    assert schedule(cron).cron == " ".join(cron.split())


@pytest.mark.parametrize(
    "cron",
    [
        "99 99 * * *",
        "60 * * * *",
        "* 24 * * *",
        "* * 0 * *",
        "* * 32 * *",
        "* * * 0 *",
        "* * * 13 *",
        "* * * * 7",
        "30-10 * * * *",
        "*/0 * * * *",
        "0-70/5 * * * *",
        "*/ * * * *",
        "1,,2 * * * *",
        "-5 * * * *",
        "*-5 * * * *",
        "0 2 * *",
        "0 2 * * * *",
        "mon * * * *",
    ],
)
def test_un_cron_fuera_de_rango_o_mal_formado_se_rechaza(cron):
    with pytest.raises(ValidationError):
        schedule(cron)
