"""BACKUP-003: `restic cat config` leyó el repositorio."""

from ens_evidence.connectors.restic.checks import backup_003_repository_accessible as check
from ens_evidence.models.enums import CheckStatus

from .check_data import evidence, repository, run, section
from .fake_restic import REPOSITORY_ID


def test_un_repositorio_accesible_pasa_y_su_sujeto_es_su_id():
    result = run(check, evidence())

    assert result.status is CheckStatus.PASS
    assert result.subject == REPOSITORY_ID


def test_un_repositorio_inexistente_falla():
    result = run(check, evidence(repository=repository(exists=False), repository_id=None))

    assert result.status is CheckStatus.FAIL
    assert "no existe" in result.reason


def test_una_existencia_no_interpretable_es_unknown():
    item = {"repository_id": REPOSITORY_ID, "backend": "remote"}

    assert run(check, evidence(repository=section([item]))).status is CheckStatus.UNKNOWN


def test_una_seccion_repository_sin_datos_es_unknown():
    assert run(check, evidence(repository=section([]))).status is CheckStatus.UNKNOWN


def test_contrasena_incorrecta_o_sin_credenciales_es_error_y_no_pass():
    result = run(
        check,
        evidence(
            collection_status="ERROR", error="contraseña del repositorio incorrecta (código 12)"
        ),
    )

    assert result.status is CheckStatus.ERROR
    assert "12" in result.reason
