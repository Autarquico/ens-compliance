from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from ens_evidence.backup import BackupSecrets, Restic, list_checks, list_restores
from ens_evidence.backup.restore_test import RestoreTest, restore_test_key

from .conftest import PASSWORD, build_backed_up, ens, restore_backed_up

pytestmark = pytest.mark.usefixtures("closed_stdin")

ASSET = {
    "id": "APP-001",
    "type": "application",
    "deployment": "deployed",
    "service": "web",
    "domain": "app.example.org",
    "repo": "org/app",
    "database": "DB-001",
}


def tree(directory: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(directory)): path.read_bytes() if path.is_file() else b""
        for path in sorted(directory.rglob("*"))
    }


@pytest.fixture(scope="module")
def template(real_restic, tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("backed-up")
    build_backed_up(root, assets=[ASSET])
    return root


@pytest.fixture
def backed_up(template, settings, repository, tmp_path):
    restore_backed_up(template, tmp_path)
    return Restic(str(repository), BackupSecrets(PASSWORD))


def test_leer_el_id_no_cambia_el_repositorio_ni_deja_locks(backed_up, repository):
    before = tree(repository)

    repository_id = backed_up.repository_id()

    assert len(repository_id) == 64
    assert tree(repository) == before
    assert list((repository / "locks").iterdir()) == []


@pytest.mark.parametrize(
    ("options", "scope"),
    [([], "metadata"), (["--read-data-subset", "50%"], "subset:50%"), (["--read-data"], "all")],
)
def test_backup_check_sobre_un_repositorio_sano_se_completa_con_su_alcance(
    settings, workspace, backed_up, options, scope
):
    result = ens(settings, "backup", "check", *options)

    assert result.exit_code == 0, result.output
    (check,), unreadable = list_checks(workspace)
    assert unreadable == []
    assert (check.status, check.scope, check.num_errors, check.broken_packs) == (
        "completed",
        scope,
        0,
        0,
    )
    assert check.repository_id == backed_up.repository_id()


def test_backup_check_con_un_pack_corrupto_falla_con_recuento(
    settings, workspace, backed_up, repository
):
    pack = next(path for path in (repository / "data").rglob("*") if path.is_file())
    pack.chmod(0o600)
    pack.write_bytes(b"corrupto" * 64)

    result = ens(settings, "backup", "check", "--read-data")

    assert result.exit_code == 1
    (check,), _ = list_checks(workspace)
    assert check.status == "failed"
    assert (check.num_errors or 0) + (check.broken_packs or 0) >= 1


def test_restauracion_y_restore_test_se_encadenan_de_extremo_a_extremo(
    settings, workspace, backed_up, tmp_path
):
    restored = ens(
        settings, "backup", "restore", "--snapshot", "latest", "--target", str(tmp_path / "r")
    )
    assert restored.exit_code == 0, restored.output
    (restore,), _ = list_restores(workspace)
    now = datetime.now(UTC)
    declaration = tmp_path / "rt.yaml"
    declaration.write_text(
        yaml.safe_dump(
            {
                "test_id": "RT-REAL",
                "system": "APP-001",
                "performed_at": now.isoformat(),
                "restore_id": restore.restore_id,
                "isolated_target": True,
                "backup": {"snapshot_id": restore.snapshot_id},
                "result": {
                    "restored": True,
                    "integrity_verified": True,
                    "application_started": True,
                },
                "review": {
                    "approved": True,
                    "reviewer": "Ana Gil",
                    "role": "security_officer",
                    "reviewed_at": now.isoformat(),
                    "valid_until": (now + timedelta(days=90)).isoformat(),
                },
            }
        )
    )

    result = ens(settings, "backup", "restore-test", "record", str(declaration))

    assert result.exit_code == 0, result.output
    saved = RestoreTest.model_validate(
        yaml.safe_load(workspace.read_bytes(restore_test_key("RT-REAL")))
    )
    assert saved.repository_id == backed_up.repository_id() == restore.repository_id
