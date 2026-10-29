"""BACKUP-005 no pasa sin contrastar el restore test con el repositorio actual."""

from ens_evidence.connectors.restic.checks import backup_005_restore_test as check
from ens_evidence.models.enums import CheckStatus

from .check_data import run
from .fake_restic import SNAPSHOT_ID, snapshot_json
from .record_data import backup_run, restore_record, restore_test

FABRICATED = "7e" * 32


def collect_test(collect, restic, *, in_repository=(SNAPSHOT_ID,), **records):
    restic.with_snapshots(*(snapshot_json(i) for i in in_repository))
    return collect(runs=[backup_run()], **records)


def test_el_caso_sano_pasa_con_el_snapshot_en_el_repositorio(collect, restic):
    collected = collect_test(
        collect, restic, restores=[restore_record()], restore_tests=[restore_test()]
    )

    assert run(check, collected).status is CheckStatus.PASS


def test_un_par_restauracion_test_sobre_un_snapshot_inexistente_no_pasa(collect, restic):
    collected = collect_test(
        collect,
        restic,
        restores=[restore_record(snapshot_id=FABRICATED)],
        restore_tests=[restore_test(snapshot_id=FABRICATED)],
    )

    item = collected.data["sections"]["restore_tests"]["items"][0]
    assert item["snapshot_in_repository"] is False
    assert run(check, collected).status is CheckStatus.UNKNOWN


def test_dos_restore_tests_que_citan_la_misma_restauracion_no_pasan(collect, restic):
    tests = [restore_test("t1"), restore_test("t2")]
    collected = collect_test(collect, restic, restores=[restore_record()], restore_tests=tests)

    items = collected.data["sections"]["restore_tests"]["items"]
    assert [i["restore_linked"] for i in items] == [False, False]
    assert run(check, collected).status is CheckStatus.UNKNOWN
