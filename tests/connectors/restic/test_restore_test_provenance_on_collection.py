"""Un restore test solo cuenta si pasó por `restore-test record` (evidencia, inventario)."""

from datetime import timedelta

import pytest

from ens_evidence.backup import dump
from ens_evidence.backup.restore_test import restore_test_key
from ens_evidence.connectors.restic.connector import read_records
from ens_evidence.evidence import import_manual
from ens_evidence.workspace import LocalStore

from .fake_restic import NOW, snapshot_json
from .record_data import restore_record, restore_test

AGE = timedelta(days=10)
INVENTORY = {"bk-diario"}


@pytest.fixture
def store(tmp_path) -> LocalStore:
    return LocalStore(tmp_path / "ws")


def _place(store, *, manual: bool, system: str = "bk-diario") -> None:
    test = restore_test(age=AGE).model_copy(update={"system": system})
    data = dump(test)
    store.create_bytes(restore_test_key(test.test_id), data)
    if manual:
        import_manual(store, data, asset_id=system, submitted_by="Ana", now=NOW)


def _item(restic, collect, store, inventoried=INVENTORY):
    restic.with_snapshots(snapshot_json())
    records = read_records(store, inventoried)
    section = collect(
        restores=[restore_record(age=AGE + timedelta(hours=1))],
        restore_tests=records.restore_tests,
        without_manual_evidence=records.without_manual_evidence,
        without_inventoried_system=records.without_inventoried_system,
    ).data["sections"]["restore_tests"]
    (item,) = section["items"]
    return item


def test_un_restore_registrado_por_el_camino_oficial_se_ata(restic, collect, store):
    _place(store, manual=True)

    item = _item(restic, collect, store)

    assert item["restore_link_problem"] is None
    assert (item["manual_evidence"], item["system_inventoried"]) == (True, True)


def test_un_restore_test_colocado_a_mano_sin_evidencia_manual_no_se_ata(restic, collect, store):
    _place(store, manual=False)

    item = _item(restic, collect, store)

    assert item["manual_evidence"] is False
    assert item["restore_linked"] is False
    assert "evidencia manual" in item["restore_link_problem"]


def test_un_restore_test_con_system_no_inventariado_no_se_ata(restic, collect, store):
    _place(store, manual=True, system="no-existe")

    item = _item(restic, collect, store)

    assert item["system_inventoried"] is False
    assert item["restore_linked"] is False
    assert "inventario" in item["restore_link_problem"]


def test_sin_la_seccion_snapshots_el_snapshot_no_se_da_por_ausente(restic, collect, store):
    _place(store, manual=True)
    restic.snapshots = (1, "")
    records = read_records(store, INVENTORY)

    section = collect(
        restores=[restore_record(age=AGE + timedelta(hours=1))],
        restore_tests=records.restore_tests,
    ).data["sections"]["restore_tests"]
    (item,) = section["items"]

    assert item["snapshot_in_repository"] is None
    assert item["restore_linked"] is False
    assert "no se pudo comprobar el snapshot" in item["restore_link_problem"]
