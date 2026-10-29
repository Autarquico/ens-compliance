"""Scenarios de la spec `collector-restic` sobre la recogida con ejecutor falso."""

from datetime import timedelta

import pytest

from ens_evidence.connectors.restic import ResticConnector
from ens_evidence.connectors.restic.checks import (
    ALL_CHECKS,
    backup_002_no_errors,
    backup_005_restore_test,
)
from ens_evidence.connectors.restic.process import ResticUnavailable
from ens_evidence.models.contracts import CheckResult
from ens_evidence.models.enums import CheckStatus, CollectionStatus
from ens_evidence.workspace.settings import BackupSettings

from .fake_restic import NOW, REPOSITORY, snapshot_json
from .record_data import (
    backup_run,
    healthy_records,
    integrity_check,
    restic_run,
    restore_record,
    restore_test,
)

EVIDENCE_ID = "ev-restic-2"
NAMES = ["001", "002", "003", "004", "005", "006"]


def run_all(evidence) -> dict[str, CheckResult]:
    results = {}
    for check in ALL_CHECKS:
        (result,) = check(evidence, EVIDENCE_ID, NOW)
        CheckResult.model_validate(result.model_dump())
        results[result.check_id.removeprefix("restic.backup_")] = result
    return results


@pytest.fixture
def healthy(collect, restic):
    restic.with_snapshots(snapshot_json())
    return lambda **changes: collect(**{**healthy_records(), **changes})


def test_un_workspace_sano_hace_pasar_los_seis_checks_y_todos_citan_la_evidencia(healthy):
    results = run_all(healthy())

    assert {r.status for r in results.values()} == {CheckStatus.PASS}
    assert {tuple(r.evidence_ids) for r in results.values()} == {(EVIDENCE_ID,)}


class TestBackupFallido:
    """Given una ejecución de backup con error; When se evalúan los checks; Then 002 da FAIL."""

    def test_backup_002_da_fail_y_aparece_como_hallazgo_con_motivo(self, healthy):
        runs = [backup_run("r1", age=timedelta(hours=26)), backup_run("r2", "failed")]

        (result,) = backup_002_no_errors(healthy(runs=runs), EVIDENCE_ID, NOW)

        assert result.status is CheckStatus.FAIL
        assert result.reason
        assert result.evidence_ids == [EVIDENCE_ID]
        assert result.asset_id == "bk-diario"

    def test_una_ejecucion_que_quedo_en_started_tambien_da_fail(self, healthy):
        runs = [backup_run("r1", age=timedelta(hours=26)), backup_run("r2", "started")]

        (result,) = backup_002_no_errors(healthy(runs=runs), EVIDENCE_ID, NOW)

        assert result.status is CheckStatus.FAIL

    def test_el_fallo_no_arrastra_a_los_checks_de_acceso_y_externa(self, healthy):
        runs = [backup_run("r1", "failed")]

        results = run_all(healthy(runs=runs))

        assert results["002_no_errors"].status is CheckStatus.FAIL
        assert results["003_repository_accessible"].status is CheckStatus.PASS
        assert results["006_offsite"].status is CheckStatus.PASS


class TestRestoreCaducado:
    """Given el último restore test fuera de vigencia; When se evalúa 005; Then da FAIL."""

    def test_backup_005_da_fail_con_un_restore_test_mas_viejo_que_la_vigencia(self, healthy):
        old = timedelta(days=120)
        records = {
            "restores": [restore_record(age=old)],
            "restore_tests": [restore_test(age=old)],
        }

        (result,) = backup_005_restore_test(healthy(**records), EVIDENCE_ID, NOW)

        assert result.status is CheckStatus.FAIL
        assert "90 d" in result.reason
        assert result.subject == "t1"

    def test_con_la_revision_caducada_tambien_da_fail(self, healthy):
        test = restore_test(age=timedelta(days=10))
        review = test.review.model_copy(update={"valid_until": NOW - timedelta(days=1)})
        records = {"restore_tests": [test.model_copy(update={"review": review})]}

        (result,) = backup_005_restore_test(healthy(**records), EVIDENCE_ID, NOW)

        assert result.status is CheckStatus.FAIL


@pytest.mark.parametrize("check", ALL_CHECKS, ids=NAMES)
@pytest.mark.parametrize("cat", [(12, ""), (1, "")], ids=["contrasena-incorrecta", "otro-error"])
def test_con_la_recogida_global_en_error_ningun_check_da_pass(healthy, restic, check, cat):
    restic.cat = cat

    evidence = healthy()
    (result,) = check(evidence, EVIDENCE_ID, NOW)

    assert evidence.collection_status is CollectionStatus.ERROR
    assert result.status is CheckStatus.ERROR
    assert result.collection_status is CollectionStatus.ERROR


def test_con_restic_inaccesible_ningun_check_da_pass():
    def unavailable(args):
        raise ResticUnavailable("not_installed")

    evidence = ResticConnector(
        backup=BackupSettings(restic_repository=REPOSITORY), executor=unavailable
    ).collect(restic_run(**healthy_records()))

    assert {r.status for r in run_all(evidence).values()} == {CheckStatus.ERROR}


def test_un_repositorio_inexistente_falla_003_001_y_006_y_ningun_check_da_pass(healthy, restic):
    restic.cat = (10, "")

    results = run_all(healthy())

    assert results["003_repository_accessible"].status is CheckStatus.FAIL
    assert results["001_recent"].status is CheckStatus.FAIL
    assert results["006_offsite"].status is CheckStatus.FAIL
    assert CheckStatus.PASS not in {r.status for r in results.values()}


def test_los_registros_de_otro_repositorio_no_hacen_pasar_002_004_ni_005(healthy):
    other = "cd" * 32
    records = {
        "runs": [backup_run(repository_id=other)],
        "checks": [integrity_check(repository_id=other)],
        "restores": [restore_record(repository_id=other)],
        "restore_tests": [restore_test(repository_id=other)],
    }

    results = run_all(healthy(**records))

    for name in ("002_no_errors", "004_integrity", "005_restore_test"):
        assert results[name].status is CheckStatus.UNKNOWN
    assert results["001_recent"].status is not CheckStatus.PASS


def test_un_snapshot_creado_fuera_de_la_herramienta_no_hace_pasar_001(healthy, restic):
    restic.with_snapshots(snapshot_json())

    results = run_all(healthy(runs=[], restores=[]))

    assert results["001_recent"].status is CheckStatus.UNKNOWN
    assert results["002_no_errors"].status is CheckStatus.UNKNOWN


def test_un_repositorio_local_hace_fallar_006_en_la_recogida_real(restic):
    local = ResticConnector(
        backup=BackupSettings(restic_repository="/repos/copias"), executor=restic, clock=lambda: NOW
    ).collect(restic_run(**healthy_records()))

    assert run_all(local)["006_offsite"].status is CheckStatus.FAIL


def test_sin_declaracion_offsite_006_requiere_revision_humana(collect):
    evidence = collect(offsite=False, **healthy_records())

    assert run_all(evidence)["006_offsite"].status is CheckStatus.UNKNOWN
