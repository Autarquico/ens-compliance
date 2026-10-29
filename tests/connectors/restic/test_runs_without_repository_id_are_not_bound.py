"""Un registro sin `repository_id` no prueba nada: la cadena del repositorio solo es diagnóstico."""

from ens_evidence.connectors.restic.checks import backup_001_recent, backup_002_no_errors
from ens_evidence.models.enums import CheckStatus

from .check_data import run
from .fake_restic import REPOSITORY, SNAPSHOT_ID, snapshot_json
from .record_data import backup_run


def unidentified_run(run_id="r1"):
    record = backup_run(run_id, repository_id=None)
    return record.model_copy(update={"repository": REPOSITORY})


def test_un_run_completado_sin_id_con_snapshot_coincidente_no_da_pass_en_001_ni_002(
    collect, restic
):
    restic.with_snapshots(snapshot_json())

    collected = collect(runs=[unidentified_run()])

    assert run(backup_001_recent, collected).status is CheckStatus.UNKNOWN
    result = run(backup_002_no_errors, collected)
    assert result.status is CheckStatus.UNKNOWN
    assert "sin id de repositorio" in result.reason


def test_el_snapshot_citado_por_un_run_sin_id_es_ajeno(collect, restic):
    restic.with_snapshots(snapshot_json())

    snapshots = collect(runs=[unidentified_run()]).data["sections"]["snapshots"]

    assert snapshots["items"][0]["foreign"] is True
    assert snapshots["summary"]["foreign_snapshots"] == [SNAPSHOT_ID]


def test_la_coincidencia_por_cadena_queda_como_diagnostico_en_el_resumen_de_runs(section):
    summary = section("runs", runs=[unidentified_run("r1")])["summary"]

    assert summary["unbound_with_matching_repository_string"] == ["r1"]
