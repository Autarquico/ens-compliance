import json

import pytest

from ens_evidence.backup import BackupError, BackupSecrets, Restic
from ens_evidence.backup.restic import CheckOutcome

from .conftest import CANARY, PASSWORD, REPOSITORY_ID


@pytest.fixture
def restic(scripted_restic, repository) -> Restic:
    return Restic(str(repository), BackupSecrets(PASSWORD))


def summary(**fields) -> str:
    return json.dumps({"message_type": "summary", **fields}) + "\n"


def test_repository_id_solo_ejecuta_una_lectura_sin_lock_ni_cache(scripted_restic, restic):
    assert restic.repository_id() == REPOSITORY_ID

    assert scripted_restic.calls() == [["--no-lock", "--no-cache", "cat", "config"]]


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({}, ["check", "--json"]),
        ({"read_data_subset": "10%"}, ["check", "--json", "--read-data-subset", "10%"]),
        ({"read_data": True}, ["check", "--json", "--read-data"]),
    ],
)
def test_check_usa_el_lock_normal_y_json(scripted_restic, restic, kwargs, expected):
    scripted_restic.reply("check", out=summary(num_errors=0, broken_packs=None))

    restic.check(**kwargs)

    assert scripted_restic.calls() == [expected]


def test_check_resume_errores_y_cuenta_los_packs_rotos(scripted_restic, restic):
    scripted_restic.reply(
        "check", out=summary(num_errors=2, broken_packs=["a" * 64, "b" * 64, "c" * 64]), code=1
    )

    assert restic.check() == CheckOutcome(exit_code=1, num_errors=2, broken_packs=3)


def test_check_sin_resumen_no_inventa_cifras(scripted_restic, restic):
    scripted_restic.reply("check", out=f"{CANARY}\n", err=CANARY, code=1)

    assert restic.check() == CheckOutcome(exit_code=1, num_errors=None, broken_packs=None)


def test_repository_id_sin_id_valido_no_cita_la_salida_de_restic(scripted_restic, restic):
    scripted_restic.reply("cat", out=json.dumps({"id": CANARY}))

    with pytest.raises(BackupError) as raised:
        restic.repository_id()

    assert CANARY not in str(raised.value)


def test_fallo_de_lectura_no_filtra_la_contrasena(scripted_restic, restic):
    scripted_restic.reply("cat", err=f"clave {PASSWORD}", code=12)

    with pytest.raises(BackupError) as raised:
        restic.repository_id()

    assert PASSWORD not in str(raised.value)


def test_fallo_de_lectura_no_cita_stderr_de_restic(scripted_restic, restic):
    scripted_restic.reply("cat", err=CANARY, code=1)

    with pytest.raises(BackupError) as raised:
        restic.repository_id()

    assert CANARY not in str(raised.value)
