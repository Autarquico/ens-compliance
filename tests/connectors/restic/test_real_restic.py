"""Extremo a extremo con restic real sobre un repositorio local temporal (solo lectura)."""

import os
import shutil
import subprocess
from datetime import UTC, datetime, timedelta

import pytest
import yaml

from ens_evidence.backup.records import list_restores
from ens_evidence.connectors.restic.checks import ALL_CHECKS
from ens_evidence.models.contracts import CheckResult
from ens_evidence.models.enums import CheckStatus, CollectionStatus

from .cli_support import ens, run_collect, snapshot_tree, stored_result, write_workspace
from .fake_restic import PASSWORD

EVIDENCE_ID = "ev-real-1"


@pytest.fixture(scope="module")
def home(tmp_path_factory):
    return tmp_path_factory.mktemp("home")


@pytest.fixture(scope="module", autouse=True)
def isolated_environment(real_restic, home):
    """Contraseña por entorno y HOME propio, para ver si restic escribe caché fuera del repo."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("HOME", str(home))
        patch.setenv("XDG_CACHE_HOME", str(home / ".cache"))
        patch.setenv("ENS_BACKUP_RESTIC_PASSWORD", PASSWORD)
        yield


def declare_restore_test(tmp_path, store) -> str:
    (restore,), _unreadable = list_restores(store)
    moment = restore.finished_at
    path = tmp_path / "restore-test.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "test_id": "rt-real-1",
                "system": "bk-diario",
                "performed_at": moment.isoformat(),
                "restore_id": restore.restore_id,
                "isolated_target": True,
                "backup": {"snapshot_id": restore.snapshot_id},
                "result": {
                    "restored": True,
                    "integrity_verified": True,
                    "application_started": "not_applicable",
                    "application_justification": "Solo se restauran ficheros de evidencias",
                },
                "review": {
                    "approved": True,
                    "reviewer": "Ana",
                    "role": "security_officer",
                    "reviewed_at": moment.isoformat(),
                    "valid_until": (moment + timedelta(days=90)).isoformat(),
                },
            }
        )
    )
    return str(path)


@pytest.fixture(scope="module")
def backed_up(tmp_path_factory):
    """(settings, store, repositorio) con una copia, un check, una restauración y su restore test.

    Se crea una vez por módulo (`init` tarda segundos); ningún test cambia el repositorio.
    """
    tmp_path = tmp_path_factory.mktemp("real")
    repository = tmp_path / "repo"
    settings, store = write_workspace(tmp_path / "base", str(repository))
    for args in (
        ("backup", "init"),
        ("backup", "run"),
        ("backup", "check", "--read-data-subset", "10%"),
        ("backup", "restore", "--snapshot", "latest", "--target", str(tmp_path / "restaurado")),
    ):
        result = ens(settings, *args)
        assert result.exit_code == 0, (args, result.output)
    declaration = declare_restore_test(tmp_path, store)
    result = ens(settings, "backup", "restore-test", "record", declaration)
    assert result.exit_code == 0, result.output
    return settings, store, repository


def evaluate(evidence) -> dict[str, CheckResult]:
    now = datetime.now(UTC)
    results = [r for check in ALL_CHECKS for r in check(evidence, EVIDENCE_ID, now)]
    return {r.check_id.removeprefix("restic.backup_"): r for r in results}


def test_la_recogida_no_cambia_el_repositorio_ni_crea_cache_ni_locks(backed_up, home):
    settings, _, repository = backed_up
    repository_before = snapshot_tree(repository)
    home_before = snapshot_tree(home)

    result = run_collect(settings)

    assert result.exit_code == 0, result.output
    assert snapshot_tree(repository) == repository_before
    assert snapshot_tree(home) == home_before
    assert not list((repository / "locks").iterdir())


def test_la_recogida_solo_anade_evidencia_al_workspace(backed_up):
    settings, _, _ = backed_up
    root = settings.parent / "ws"
    before = snapshot_tree(root)
    assert before

    run_collect(settings)

    after = snapshot_tree(root)
    assert all(after[key] == digest for key, digest in before.items())
    assert {key for key in after if key not in before}
    assert all(key.startswith("evidence") for key in after if key not in before)


def test_con_restic_real_cinco_checks_pasan_y_el_repositorio_local_no_es_copia_externa(backed_up):
    settings, store, _ = backed_up
    assert run_collect(settings).exit_code == 0

    stored, _ = stored_result(store)
    results = evaluate(stored)

    assert stored.collection_status is CollectionStatus.SUCCESS
    assert {key: r.status for key, r in results.items()} == {
        "001_recent": CheckStatus.PASS,
        "002_no_errors": CheckStatus.PASS,
        "003_repository_accessible": CheckStatus.PASS,
        "004_integrity": CheckStatus.PASS,
        "005_restore_test": CheckStatus.PASS,
        "006_offsite": CheckStatus.FAIL,
    }
    assert results["005_restore_test"].subject == "rt-real-1"
    assert stored.data["sections"]["snapshots"]["summary"]["foreign_snapshots"] == []


def test_un_repositorio_inexistente_sale_con_0_y_el_check_003_falla(tmp_path):
    repository = tmp_path / "no-existe"
    settings, store = write_workspace(tmp_path / "ws-vacio", str(repository))

    result = run_collect(settings)

    assert result.exit_code == 0, result.output
    stored, _ = stored_result(store)
    assert stored.data["sections"]["repository"]["items"][0]["exists"] is False
    assert evaluate(stored)["003_repository_accessible"].status is CheckStatus.FAIL
    assert not repository.exists()


def test_contrasena_incorrecta_sale_con_1_y_ningun_check_da_pass(backed_up, monkeypatch):
    settings, store, _ = backed_up
    monkeypatch.setenv("ENS_BACKUP_RESTIC_PASSWORD", "otra-contrasena-distinta")

    result = run_collect(settings)

    assert result.exit_code == 1
    assert "12" in result.output
    stored, _ = stored_result(store)
    assert stored.collection_status is CollectionStatus.ERROR
    assert {r.status for r in evaluate(stored).values()} == {CheckStatus.ERROR}
    assert "otra-contrasena-distinta" not in result.output + stored.model_dump_json()


def test_con_restic_real_un_snapshot_creado_a_mano_se_informa_como_ajeno(backed_up, tmp_path):
    settings, store, repository = backed_up
    copy = tmp_path / "repo-copia"
    shutil.copytree(repository, copy)
    extra = tmp_path / "extra.txt"
    extra.write_text("fuera de la herramienta")
    subprocess.run(  # noqa: S603 - fixture de test con restic real
        ["restic", "--repo", str(copy), "backup", "--tag", "ens-evidence", str(extra)],
        env={**os.environ, "RESTIC_PASSWORD": PASSWORD},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=True,
    )
    other = yaml.safe_load(settings.read_text())
    other["backup"]["restic_repository"] = str(copy)
    other_settings = tmp_path / "settings-copia.yaml"
    other_settings.write_text(yaml.safe_dump(other))

    assert run_collect(other_settings).exit_code == 0

    stored, _ = stored_result(store)
    summary = stored.data["sections"]["snapshots"]["summary"]
    assert summary["count"] == 2
    assert len(summary["foreign_snapshots"]) == 1
    assert evaluate(stored)["001_recent"].status is CheckStatus.PASS
