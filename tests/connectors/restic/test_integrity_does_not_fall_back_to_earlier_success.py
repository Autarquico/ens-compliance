"""Un último `backup check` fallido sin id nunca deja volver al éxito anterior (BACKUP-004)."""

from datetime import timedelta

from ens_evidence.connectors.restic.checks import backup_004_integrity
from ens_evidence.models.enums import CheckStatus

from .check_data import run
from .record_data import integrity_check


def test_un_ultimo_intento_failed_sin_id_no_cae_en_el_exito_anterior(collect):
    collected = collect(
        checks=[
            integrity_check("c1", age=timedelta(days=3)),
            integrity_check("c2", "failed", age=timedelta(hours=1), repository_id=None),
        ]
    )

    result = run(backup_004_integrity, collected)

    assert result.status is CheckStatus.UNKNOWN
    assert "sin id de repositorio" in result.reason
