"""Atado de las ejecuciones al repositorio actual y su efecto en BACKUP-001/002."""

from datetime import timedelta

from ens_evidence.connectors.restic.checks import backup_001_recent, backup_002_no_errors
from ens_evidence.models.enums import CheckStatus

from .check_data import run
from .fake_restic import REPOSITORY, SNAPSHOT_ID, snapshot_json
from .record_data import backup_run


def old_run(run_id="r1", status="completed", *, repository=REPOSITORY, **kwargs):
    """Ejecución anterior a `repository_id`: solo conserva la cadena del repositorio."""
    record = backup_run(run_id, status, repository_id=None, **kwargs)
    return record.model_copy(update={"repository": repository})


def test_una_ejecucion_fallida_sin_id_del_mismo_repositorio_da_fail_en_002(collect):
    collected = collect(runs=[backup_run("r0", age=timedelta(hours=30)), old_run("r1", "failed")])

    assert run(backup_002_no_errors, collected).status is CheckStatus.FAIL


def test_una_ejecucion_fallida_sin_id_de_otro_repositorio_da_unknown_en_002(collect):
    collected = collect(runs=[old_run("r1", "failed", repository="s3:otro/bucket")])

    assert run(backup_002_no_errors, collected).status is CheckStatus.UNKNOWN


def test_las_ejecuciones_sin_id_del_mismo_repositorio_salen_atadas(section):
    items = section("runs", runs=[old_run("r1"), old_run("r2", repository="s3:otro/bucket")])[
        "items"
    ]

    assert {i["run_id"]: i["bound"] for i in items} == {"r1": True, "r2": False}


def test_un_snapshot_citado_por_una_ejecucion_de_otro_repositorio_es_ajeno(collect, restic):
    restic.with_snapshots(snapshot_json())
    other = backup_run("r1", repository_id="cd" * 32)

    collected = collect(runs=[other])

    snapshots = collected.data["sections"]["snapshots"]
    assert snapshots["items"][0]["foreign"] is True
    assert snapshots["summary"]["foreign_snapshots"] == [SNAPSHOT_ID]
    assert run(backup_001_recent, collected).status is CheckStatus.UNKNOWN
