import pytest

from ens_evidence.backup import list_runs
from ens_evidence.backup.records import BackupRun, dump, run_key
from ens_evidence.backup.transfer import import_all
from ens_evidence.workspace import LocalStore, WorkspaceError

from .conftest import ens
from .test_snapshot_trust import NOW, RUN_ID, STARTED, marker, snapshot_folder

FORGED_SNAPSHOT_ID = "ffff0000"


def forged_record(status: str) -> bytes:
    if status == "started":
        run = BackupRun(run_id=RUN_ID, status="started", started_at=STARTED, repository="falso")
    else:
        run = BackupRun(
            run_id=RUN_ID,
            status="completed",
            started_at=STARTED,
            finished_at=STARTED,
            repository="falso",
            snapshot_id=FORGED_SNAPSHOT_ID,
            inventory={"scope/a.yaml": "0" * 64},
        )
    return dump(run)


def folder_with_own_record(root, status: str) -> LocalStore:
    folder = snapshot_folder(root, inventories=[RUN_ID], marker=marker())
    folder.write_bytes(run_key(RUN_ID), forged_record(status))
    return folder


def contents(store) -> dict[str, bytes]:
    return {key: store.read_bytes(key) for key in store.list("")}


@pytest.mark.parametrize("status", ["completed", "started"])
def test_import_con_el_registro_propio_anadido_aborta_sin_escribir_en_local(tmp_path, status):
    folder = folder_with_own_record(tmp_path / "f", status)
    destination = LocalStore(tmp_path / "ws")

    with pytest.raises(WorkspaceError, match="no se importa nada") as raised:
        import_all(destination, folder, NOW)

    assert run_key(RUN_ID) in str(raised.value)
    assert destination.list("") == []


@pytest.mark.parametrize("status", ["completed", "started"])
def test_import_a_s3_con_el_registro_propio_anadido_aborta_sin_escribir(s3, tmp_path, status):
    folder = folder_with_own_record(tmp_path / "f", status)
    s3.write_bytes("scope/previo.yaml", b"previo")
    before = contents(s3)

    with pytest.raises(WorkspaceError, match="no se importa nada") as raised:
        import_all(s3, folder, NOW)

    assert run_key(RUN_ID) in str(raised.value)
    assert contents(s3) == before


def test_import_limpio_reconstruye_siempre_el_registro_propio_desde_el_marcador(tmp_path):
    folder = snapshot_folder(tmp_path / "f", inventories=[RUN_ID], marker=marker())
    destination = LocalStore(tmp_path / "ws")

    import_all(destination, folder, NOW)

    (run,) = list_runs(destination)[0]
    assert run.run_id == RUN_ID
    assert run.status == "completed"
    assert run.reconstructed_from_snapshot is True
    assert run.snapshot_id == marker().snapshot_id
    assert run.repository != "falso"


def test_import_por_cli_con_el_registro_propio_falsificado_sale_con_error(settings, tmp_path):
    folder_with_own_record(tmp_path / "f", "completed")

    result = ens(settings, "workspace", "import", "--all", str(tmp_path / "f"))

    assert result.exit_code != 0
    assert run_key(RUN_ID) in result.output
    assert LocalStore(tmp_path / "ws").list("") == []
