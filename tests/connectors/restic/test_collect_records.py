"""Registros del workspace: atados (`bound`) o no al repositorio actual; los ilegibles se listan."""

import json
from datetime import timedelta

import pytest
import yaml

from ens_evidence.backup.records import (
    CanaryResult,
    ImmutabilityCheck,
    check_key,
    restore_key,
    run_key,
)
from ens_evidence.backup.restore_test import restore_test_key
from ens_evidence.connectors.restic.connector import read_records
from ens_evidence.workspace import LocalStore

from .fake_restic import NOW, OTHER_REPOSITORY_ID, REPOSITORY, REPOSITORY_ID, snapshot_json
from .record_data import (
    backup_run,
    integrity_check,
    restic_run,
    restore_record,
    restore_test,
)

SECTIONS = [
    ("runs", "runs", backup_run),
    ("integrity_checks", "checks", integrity_check),
    ("restores", "restores", restore_record),
    ("restore_tests", "restore_tests", restore_test),
]


@pytest.mark.parametrize(("name", "key", "build"), SECTIONS, ids=[s[0] for s in SECTIONS])
def test_un_registro_del_repositorio_actual_sale_con_bound_true(section, name, key, build):
    items = section(name, **{key: [build()]})["items"]

    assert [i["bound"] for i in items] == [True]
    assert [i["repository_id"] for i in items] == [REPOSITORY_ID]


@pytest.mark.parametrize(("name", "key", "build"), SECTIONS, ids=[s[0] for s in SECTIONS])
def test_un_registro_de_otro_repositorio_sale_con_bound_false_y_no_se_omite(
    section, name, key, build
):
    items = section(name, **{key: [build(repository_id=OTHER_REPOSITORY_ID)]})["items"]

    assert [i["bound"] for i in items] == [False]
    assert [i["repository_id"] for i in items] == [OTHER_REPOSITORY_ID]


def test_una_ejecucion_sin_id_de_repositorio_sale_con_bound_false(section):
    items = section("runs", runs=[backup_run(repository_id=None)])["items"]

    assert [i["bound"] for i in items] == [False]


def test_las_ejecuciones_informan_estado_y_tiempos_en_utc_sin_el_texto_del_error(section):
    runs = [backup_run("r1", "failed", age=timedelta(hours=5)), backup_run("r2", "started")]

    items = {i["run_id"]: i for i in section("runs", runs=runs)["items"]}

    assert items["r1"]["status"] == "failed"
    assert items["r1"]["has_error"] is True
    assert "error" not in items["r1"]
    assert items["r1"]["started_at"].endswith("Z")
    assert items["r2"]["status"] == "started"
    assert items["r2"]["finished_at"] is None


def test_un_check_informa_alcance_cobertura_y_resultado(section):
    check = integrity_check(scope="subset:25%")

    (item,) = section("integrity_checks", checks=[check])["items"]

    assert (item["scope"], item["read_data_percent"], item["num_errors"]) == ("subset:25%", 25, 0)


def test_las_restauraciones_no_incluyen_la_ruta_de_destino(section):
    text = json.dumps(section("restores", restores=[restore_record()]))

    assert "/restauraciones/aislada" not in text


def test_un_restore_test_atado_a_una_restauracion_verificada_sale_con_restore_linked(
    restic, section
):
    restic.with_snapshots(snapshot_json())

    items = section("restore_tests", restores=[restore_record()], restore_tests=[restore_test()])[
        "items"
    ]

    assert [i["restore_linked"] for i in items] == [True]


def test_un_restore_test_cuyo_snapshot_no_esta_en_el_repositorio_sale_con_restore_linked_false(
    section,
):
    items = section("restore_tests", restores=[restore_record()], restore_tests=[restore_test()])[
        "items"
    ]

    assert [i["restore_linked"] for i in items] == [False]


@pytest.mark.parametrize(
    "restore",
    [
        None,
        restore_record(snapshot_id="9f" * 32),
        restore_record(repository_id=OTHER_REPOSITORY_ID),
        restore_record(inventory_verified=False),
    ],
    ids=["sin-restauracion", "otro-snapshot", "otro-repositorio", "inventario-no-verificado"],
)
def test_un_restore_test_no_atado_a_su_restauracion_sale_con_restore_linked_false(section, restore):
    restores = [restore] if restore else []

    items = section("restore_tests", restores=restores, restore_tests=[restore_test()])["items"]

    assert [i["restore_linked"] for i in items] == [False]


def test_las_comprobaciones_de_inmutabilidad_se_atan_por_la_cadena_del_repositorio(connector):
    def check(repository):
        return ImmutabilityCheck(
            checked_at=NOW,
            repository=repository,
            results=[CanaryResult(prefix="data/", status="protected", detail="rechazado")],
        )

    run = restic_run(immutability=[check(REPOSITORY), check("s3:otro.example.com/otra")])

    items = connector.collect(run).data["sections"]["immutability"]["items"]

    assert sorted(i["bound"] for i in items) == [False, True]


class TestRegistrosIlegibles:
    """Lectura real del workspace: un registro corrupto se lista, nunca se salta."""

    @pytest.fixture
    def store(self, tmp_path) -> LocalStore:
        return LocalStore(tmp_path / "ws")

    @staticmethod
    def write(store, key, record) -> None:
        store.write_text(key, yaml.safe_dump(record.model_dump(mode="json")))

    def test_los_ilegibles_se_listan_y_los_legibles_se_conservan(self, store, collect):
        self.write(store, run_key("buena"), backup_run("buena"))
        store.write_text(run_key("rota"), "run_id: [no cierra")
        store.write_text(check_key("rota"), "check_id: x\nstatus: inventado\n")
        store.write_text(restore_key("rota"), "- una lista\n")
        store.write_text(restore_test_key("rota"), "test_id: solo-esto\n")

        records = read_records(store)
        sections = collect(
            runs=records.runs,
            checks=records.checks,
            restores=records.restores,
            restore_tests=records.restore_tests,
            unreadable=records.unreadable,
        ).data["sections"]

        assert [i["run_id"] for i in sections["runs"]["items"]] == ["buena"]
        assert sections["runs"]["summary"]["unreadable"] == [run_key("rota")]
        assert sections["integrity_checks"]["summary"]["unreadable"] == [check_key("rota")]
        assert sections["restores"]["summary"]["unreadable"] == [restore_key("rota")]
        assert sections["restore_tests"]["summary"]["unreadable"] == [restore_test_key("rota")]

    def test_un_workspace_sin_registros_da_secciones_vacias_sin_ilegibles(self, store, collect):
        records = read_records(store)

        sections = collect(unreadable=records.unreadable).data["sections"]

        for name in ("runs", "integrity_checks", "restores", "restore_tests", "immutability"):
            assert sections[name]["items"] == []
            assert sections[name]["summary"]["unreadable"] == []
