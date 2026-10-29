"""BACKUP-006: copia externa = backend remoto accesible con declaración `offsite` vigente."""

from datetime import timedelta

import pytest

from ens_evidence.connectors.restic.checks import backup_006_offsite as check
from ens_evidence.models.enums import CheckStatus

from .check_data import HOUR, ago, evidence, repository, run
from .record_data import OFFSITE

PASS, FAIL, UNKNOWN, ERROR = (
    CheckStatus.PASS,
    CheckStatus.FAIL,
    CheckStatus.UNKNOWN,
    CheckStatus.ERROR,
)


def status(*, backend="remote", exists=True, **kwargs) -> CheckStatus:
    return run(
        check, evidence(repository=repository(exists=exists, backend=backend), **kwargs)
    ).status


def declaration(**changes) -> dict:
    return {**OFFSITE, **changes}


def test_un_destino_remoto_accesible_con_declaracion_vigente_pasa_y_aclara_el_limite():
    result = run(check, evidence())

    assert result.status is PASS
    assert "no comprobad" in result.reason


def test_un_repositorio_local_falla_aunque_declare_offsite():
    assert status(backend="local") is FAIL
    assert status(backend="local", offsite=declaration()) is FAIL


def test_un_repositorio_local_falla_sin_declaracion():
    assert status(backend="local", offsite=None) is FAIL


@pytest.mark.parametrize("offsite", [None, {}])
def test_un_destino_remoto_sin_declaracion_requiere_revision_humana(offsite):
    result = run(check, evidence(offsite=offsite))

    assert result.status is UNKNOWN
    assert "revisión humana" in result.reason


@pytest.mark.parametrize(
    "changes",
    [
        {"valid_until": ago(timedelta(days=1))},
        {"valid_until": ago()},
        {"reviewed_at": ago(-HOUR), "valid_until": ago(-2 * HOUR)},
        {"reviewed_at": "ayer"},
        {"valid_until": "2026-10-29T10:00:00"},
        {"valid_until": None},
    ],
    ids=[
        "caducada",
        "caduca-ahora",
        "revision-futura",
        "fecha-ilegible",
        "sin-zona",
        "sin-valid-until",
    ],
)
def test_una_declaracion_no_vigente_o_ilegible_es_unknown(changes):
    assert status(offsite=declaration(**changes)) is UNKNOWN


def test_la_declaracion_vigente_hasta_un_segundo_despues_aun_pasa():
    assert status(offsite=declaration(valid_until=ago(-timedelta(seconds=1)))) is PASS


def test_la_declaracion_revisada_en_este_mismo_instante_pasa():
    assert status(offsite=declaration(reviewed_at=ago())) is PASS


def test_un_backend_desconocido_requiere_revision_humana():
    assert status(backend="desconocido") is UNKNOWN


def test_un_destino_remoto_que_no_existe_falla():
    assert status(exists=False) is FAIL


def test_la_recogida_global_fallida_no_da_pass():
    assert status(collection_status="ERROR", error="x") is ERROR


def test_una_seccion_repository_ausente_no_da_pass():
    result = run(check, evidence(repository={}))

    assert result.status is ERROR
