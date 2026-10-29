"""Un restore test editado a mano cumple en la recogida las reglas de `restore-test record`."""

import json
from datetime import timedelta

from ens_evidence.backup.restore_test import MAX_DAYS_AFTER_RESTORE
from ens_evidence.connectors.restic.checks import backup_005_restore_test as check
from ens_evidence.connectors.restic.sections import read_repository
from ens_evidence.models.enums import CheckStatus

from .check_data import evidence, restore_test_item, run
from .check_data import section as check_section
from .fake_restic import REPOSITORY_ID, snapshot_json
from .record_data import restore_record, restore_test

AGE = timedelta(days=10)
LATE = timedelta(days=MAX_DAYS_AFTER_RESTORE, hours=1)


def _linked(restic, section, restores, tests):
    restic.with_snapshots(snapshot_json())
    return section("restore_tests", restores=restores, restore_tests=tests)["items"]


def test_performed_at_anterior_al_fin_de_la_restauracion_no_se_ata(restic, section):
    # el fixture termina la restauración 1 minuto después de su inicio (= NOW - AGE)
    (item,) = _linked(restic, section, [restore_record(age=AGE)], [restore_test(age=AGE)])

    assert item["restore_linked"] is False
    assert "anterior al fin" in item["restore_link_problem"]


def test_performed_at_fuera_de_la_ventana_de_7_dias_no_se_ata(restic, section):
    restore = restore_record(age=AGE + LATE)

    (item,) = _linked(restic, section, [restore], [restore_test(age=AGE)])

    assert item["restore_linked"] is False
    assert "7 días" in item["restore_link_problem"]


def test_performed_at_justo_despues_de_la_restauracion_se_ata(restic, section):
    restore = restore_record(age=AGE + timedelta(hours=1))

    (item,) = _linked(restic, section, [restore], [restore_test(age=AGE)])

    assert item["restore_link_problem"] is None
    assert item["restore_linked"] is True


def test_una_restauracion_citada_por_dos_tests_no_ata_ninguno(restic, section):
    restore = restore_record(age=AGE + timedelta(hours=1))
    tests = [restore_test("t1", age=AGE), restore_test("t2", age=AGE - timedelta(hours=2))]

    items = _linked(restic, section, [restore], tests)

    assert [i["restore_linked"] for i in items] == [False, False]
    assert all("más de un restore test" in i["restore_link_problem"] for i in items)


def test_el_check_da_unknown_con_el_motivo_del_registro():
    item = restore_test_item(linked=False, restore_link_problem="performed_at anterior")

    result = run(check, evidence(restore_tests=check_section([item], unreadable=[])))

    assert result.status is CheckStatus.UNKNOWN
    assert "performed_at anterior" in result.reason


def test_el_check_da_fail_si_el_restore_test_caducado_incumple_las_reglas():
    item = restore_test_item(age=200 * timedelta(days=1), linked=False)

    result = run(check, evidence(restore_tests=check_section([item], unreadable=[])))

    assert result.status is CheckStatus.FAIL


def test_read_repository_lee_el_id_del_repositorio_de_cat_config(restic):
    assert read_repository(restic) == REPOSITORY_ID


def test_un_id_de_repositorio_que_no_es_hex_de_64_falla_la_recogida(collect, restic):
    restic.cat = (0, json.dumps({"id": "no-hex"}))

    result = collect()

    assert "id válido" in result.data["error"]
