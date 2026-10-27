import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from ens_evidence.backup import BackupSecrets, Restic, list_runs, run_backup
from ens_evidence.backup.records import (
    SNAPSHOT_MARKER_KEY,
    SnapshotInventory,
    SnapshotMarker,
    run_key,
    snapshot_inventory_key,
)
from ens_evidence.backup.transfer import import_all
from ens_evidence.workspace import LocalStore, WorkspaceError

from .conftest import PASSWORD, ens

META = ".ens-backup"
RUN_ID = "BK-20260601T000000Z-aaaaaa"
OTHER_RUN_ID = "BK-20260601T000000Z-bbbbbb"
STARTED = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
EXPORTED = datetime(2026, 6, 1, 0, 5, tzinfo=UTC)
SNAPSHOT_TIME = datetime(2026, 6, 1, 0, 7, tzinfo=UTC)
NOW = datetime(2026, 7, 1, tzinfo=UTC)


class TickingClock:
    def __init__(self) -> None:
        self.now = STARTED

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now

    def advance(self, **delta: int) -> None:
        self.now += timedelta(**delta)


class ResticBoundary:
    """Frontera de proceso: guarda lo que restic recibiría y deja pasar el tiempo al copiar."""

    def __init__(self, clock: TickingClock) -> None:
        self._clock = clock
        self.called = False
        self.clock_at_backup: datetime | None = None
        self.inventory_seen: dict | None = None

    def backup(self, directory: Path) -> str:
        self.called = True
        self._clock.advance(minutes=10)
        self.clock_at_backup = self._clock.now
        (path,) = (directory / META).glob("*.inventory.json")
        self.inventory_seen = SnapshotInventory.model_validate_json(path.read_bytes()).model_dump()
        return "abcd1234"


def stored_run(workspace: LocalStore, run_id: str) -> dict:
    return yaml.safe_load(workspace.read_bytes(run_key(run_id)))


FILES = {"scope/a.yaml": b"alcance", "scope/b.yaml": b"sistema"}


def snapshot_folder(root: Path, *, inventories: list[str], marker: SnapshotMarker | None):
    """Carpeta como la deja `backup restore`: ficheros e inventario coherentes entre sí."""
    folder = LocalStore(root)
    for key, data in FILES.items():
        folder.write_bytes(key, data)
    for run_id in inventories:
        inventory = SnapshotInventory(
            run_id=run_id,
            started_at=STARTED,
            exported_at=EXPORTED,
            repository="repo",
            inventory={key: hashlib.sha256(data).hexdigest() for key, data in FILES.items()},
        )
        folder.write_bytes(snapshot_inventory_key(run_id), inventory.model_dump_json().encode())
    if marker:
        folder.write_bytes(SNAPSHOT_MARKER_KEY, marker.model_dump_json().encode())
    return folder


def marker(run_id: str = RUN_ID, time: datetime | None = SNAPSHOT_TIME) -> SnapshotMarker:
    return SnapshotMarker(snapshot_id="abcd1234", run_id=run_id, snapshot_time=time)


def test_el_registro_completed_termina_despues_de_la_llamada_a_restic(workspace):
    workspace.write_bytes("scope/a.yaml", b"alcance")
    clock = TickingClock()
    restic = ResticBoundary(clock)

    run = run_backup(workspace, restic, "repo", BackupSecrets(PASSWORD), clock)

    assert run.status == "completed"
    assert run.finished_at > restic.clock_at_backup
    assert stored_run(workspace, run.run_id)["finished_at"] == run.finished_at.isoformat().replace(
        "+00:00", "Z"
    )


def test_el_inventario_del_snapshot_lleva_exported_at_anterior_a_la_copia(workspace):
    workspace.write_bytes("scope/a.yaml", b"alcance")
    clock = TickingClock()
    restic = ResticBoundary(clock)

    run = run_backup(workspace, restic, "repo", BackupSecrets(PASSWORD), clock)

    seen = restic.inventory_seen
    assert "finished_at" not in seen
    assert run.started_at <= seen["exported_at"] < restic.clock_at_backup


def test_el_registro_reconstruido_usa_la_hora_del_snapshot_y_sigue_marcado(tmp_path):
    folder = snapshot_folder(tmp_path / "f", inventories=[RUN_ID], marker=marker())
    destination = LocalStore(tmp_path / "ws")

    report = import_all(destination, folder, NOW)

    (run,) = list_runs(destination)[0]
    assert run.status == "completed"
    assert run.finished_at == SNAPSHOT_TIME
    assert run.reconstructed_from_snapshot is True and run.reconstructed_at == NOW
    assert "reconstruido" in report.backup_note


def test_el_registro_reconstruido_sin_hora_de_snapshot_usa_exported_at(tmp_path):
    folder = snapshot_folder(tmp_path / "f", inventories=[RUN_ID], marker=marker(time=None))
    destination = LocalStore(tmp_path / "ws")

    import_all(destination, folder, NOW)

    (run,) = list_runs(destination)[0]
    assert run.finished_at == EXPORTED
    assert run.reconstructed_from_snapshot is True


def test_backup_run_con_claves_bajo_ens_backup_termina_failed_sin_copiar(workspace):
    workspace.write_bytes("scope/a.yaml", b"alcance")
    workspace.write_bytes(f"{META}/snapshot.json", b"{}")
    clock = TickingClock()
    restic = ResticBoundary(clock)

    run = run_backup(workspace, restic, "repo", BackupSecrets(PASSWORD), clock)

    assert run.status == "failed"
    assert META in run.error
    assert run.snapshot_id is None
    assert restic.called is False
    assert stored_run(workspace, run.run_id)["status"] == "failed"


def test_backup_run_por_cli_con_claves_bajo_ens_backup_sale_con_error(
    settings, workspace, repository
):
    workspace.write_bytes(f"{META}/x", b"x")

    result = ens(settings, "backup", "run")

    assert result.exit_code == 1
    (run,) = list_runs(workspace)[0]
    assert run.status == "failed" and run.snapshot_id is None


@pytest.mark.parametrize(
    "inventories, marker_run",
    [
        pytest.param([RUN_ID, OTHER_RUN_ID], RUN_ID, id="dos-inventarios"),
        pytest.param([OTHER_RUN_ID], RUN_ID, id="inventario-de-otra-ejecucion"),
        pytest.param([], RUN_ID, id="sin-inventario"),
    ],
)
def test_import_con_inventarios_que_no_corresponden_al_marcador_no_reconstruye(
    tmp_path, inventories, marker_run
):
    folder = snapshot_folder(tmp_path / "f", inventories=inventories, marker=marker(marker_run))
    destination = LocalStore(tmp_path / "ws")

    report = import_all(destination, folder, NOW)

    assert list_runs(destination)[0] == []
    assert report.backup_note
    assert "no se reconstruye" in report.backup_note


def test_import_sin_marcador_no_reconstruye_e_informa(tmp_path):
    folder = snapshot_folder(tmp_path / "f", inventories=[RUN_ID], marker=None)
    destination = LocalStore(tmp_path / "ws")

    report = import_all(destination, folder, NOW)

    assert list_runs(destination)[0] == []
    assert "no se reconstruye" in report.backup_note


def test_import_por_cli_informa_de_que_no_reconstruye(settings, tmp_path):
    snapshot_folder(tmp_path / "f", inventories=[RUN_ID, OTHER_RUN_ID], marker=marker())

    result = ens(settings, "workspace", "import", "--all", str(tmp_path / "f"))

    assert result.exit_code == 0, result.output
    assert "no se reconstruye" in result.output
    assert list_runs(LocalStore(tmp_path / "ws"))[0] == []


@pytest.fixture
def forged_snapshot(settings, repository, tmp_path, real_restic):
    """Snapshot real cuya carpeta trae un marcador ajeno, con o sin inventario verificable."""

    def build(*, inventory_hash: str | None) -> Path:
        assert ens(settings, "backup", "init").exit_code == 0
        folder = snapshot_folder(tmp_path / "forged", inventories=[], marker=marker())
        if inventory_hash is not None:
            inventory = SnapshotInventory(
                run_id=RUN_ID,
                started_at=STARTED,
                exported_at=EXPORTED,
                repository="repo",
                inventory={"scope/a.yaml": inventory_hash},
            )
            folder.write_bytes(snapshot_inventory_key(RUN_ID), inventory.model_dump_json().encode())
        Restic(str(repository), BackupSecrets(PASSWORD)).backup(tmp_path / "forged")
        return tmp_path / "restored"

    return build


def test_restore_no_verificado_no_deja_el_marcador_del_snapshot(settings, forged_snapshot):
    restored = forged_snapshot(inventory_hash=None)

    result = ens(settings, "backup", "restore", "--snapshot", "latest", "--target", str(restored))

    assert result.exit_code != 0
    assert "NO verificada" in result.output
    assert not (restored / SNAPSHOT_MARKER_KEY).exists()


def test_restore_con_discrepancias_no_deja_el_marcador_del_snapshot(settings, forged_snapshot):
    restored = forged_snapshot(inventory_hash="0" * 64)

    result = ens(settings, "backup", "restore", "--snapshot", "latest", "--target", str(restored))

    assert result.exit_code != 0
    assert not (restored / SNAPSHOT_MARKER_KEY).exists()


def test_import_de_una_restauracion_no_verificada_no_reconstruye(settings, forged_snapshot):
    restored = forged_snapshot(inventory_hash="0" * 64)
    ens(settings, "backup", "restore", "--snapshot", "latest", "--target", str(restored))

    result = ens(settings, "workspace", "import", "--all", str(restored))

    assert result.exit_code == 0, result.output
    assert "no se reconstruye" in result.output


def tamper_modify(root: Path) -> None:
    (root / "scope/a.yaml").write_bytes(b"alcance manipulado")


def tamper_delete(root: Path) -> None:
    (root / "scope/b.yaml").unlink()


def tamper_add(root: Path) -> None:
    (root / "scope/c.yaml").write_bytes(b"fichero ajeno")


TAMPERINGS = [
    pytest.param(tamper_modify, "scope/a.yaml", id="modificado"),
    pytest.param(tamper_delete, "scope/b.yaml", id="eliminado"),
    pytest.param(tamper_add, "scope/c.yaml", id="anadido"),
]


def populated_destination(store) -> dict[str, bytes]:
    store.write_bytes("otros/previo.txt", b"ya estaba")
    return {key: store.read_bytes(key) for key in store.list("")}


@pytest.mark.parametrize("tamper, culprit", TAMPERINGS)
def test_import_con_la_carpeta_distinta_del_inventario_aborta_sin_escribir(
    tmp_path, tamper, culprit
):
    folder = snapshot_folder(tmp_path / "f", inventories=[RUN_ID], marker=marker())
    tamper(tmp_path / "f")
    destination = LocalStore(tmp_path / "ws")
    before = populated_destination(destination)

    with pytest.raises(WorkspaceError, match="no se importa nada") as raised:
        import_all(destination, folder, NOW)

    assert culprit in str(raised.value)
    assert {key: destination.read_bytes(key) for key in destination.list("")} == before


@pytest.mark.parametrize("tamper, culprit", TAMPERINGS)
def test_import_por_cli_con_la_carpeta_distinta_del_inventario_sale_con_error(
    settings, tmp_path, tamper, culprit
):
    snapshot_folder(tmp_path / "f", inventories=[RUN_ID], marker=marker())
    tamper(tmp_path / "f")

    result = ens(settings, "workspace", "import", "--all", str(tmp_path / "f"))

    assert result.exit_code != 0
    assert culprit in result.output
    assert LocalStore(tmp_path / "ws").list("") == []


@pytest.mark.parametrize("tamper, culprit", TAMPERINGS)
def test_import_a_s3_con_la_carpeta_distinta_del_inventario_aborta_sin_escribir(
    s3, tmp_path, tamper, culprit
):
    folder = snapshot_folder(tmp_path / "f", inventories=[RUN_ID], marker=marker())
    tamper(tmp_path / "f")
    before = populated_destination(s3)

    with pytest.raises(WorkspaceError, match="no se importa nada"):
        import_all(s3, folder, NOW)

    assert {key: s3.read_bytes(key) for key in s3.list("")} == before


def test_import_limpio_importa_los_ficheros_y_reconstruye_el_registro(tmp_path):
    folder = snapshot_folder(tmp_path / "f", inventories=[RUN_ID], marker=marker())
    destination = LocalStore(tmp_path / "ws")

    report = import_all(destination, folder, NOW)

    assert {key: destination.read_bytes(key) for key in FILES} == FILES
    (run,) = list_runs(destination)[0]
    assert run.run_id == RUN_ID and run.reconstructed_from_snapshot is True
    assert "reconstruido" in report.backup_note


def test_import_de_carpeta_sin_marcador_importa_no_reconstruye_y_avisa_no_verificada(tmp_path):
    folder = LocalStore(tmp_path / "f")
    for key, data in FILES.items():
        folder.write_bytes(key, data)
    destination = LocalStore(tmp_path / "ws")

    report = import_all(destination, folder, NOW)

    assert {key: destination.read_bytes(key) for key in FILES} == FILES
    assert list_runs(destination)[0] == []
    assert "NO verificada" in report.backup_note


def test_import_por_cli_de_carpeta_sin_marcador_avisa_no_verificada(settings, tmp_path):
    folder = LocalStore(tmp_path / "f")
    folder.write_bytes("scope/a.yaml", b"alcance")

    result = ens(settings, "workspace", "import", "--all", str(tmp_path / "f"))

    assert result.exit_code == 0, result.output
    assert "NO verificada" in result.output
    assert (tmp_path / "ws" / "scope" / "a.yaml").read_bytes() == b"alcance"
