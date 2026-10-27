import ast
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ens_evidence.backup.transfer import export_all, import_all
from ens_evidence.workspace import LocalStore, WorkspaceError

from .conftest import ens, import_evidences


class RecordingStore(LocalStore):
    """Frontera de almacenamiento: anota el orden de escritura y puede fallar en una clave."""

    def __init__(self, root: Path, fail_prefix: str | None = None) -> None:
        super().__init__(root)
        self.order: list[str] = []
        self._fail_prefix = fail_prefix

    def _record(self, key: str) -> None:
        if self._fail_prefix and key.startswith(self._fail_prefix):
            raise WorkspaceError("disco lleno")
        self.order.append(key)

    def write_bytes(self, key: str, data: bytes) -> None:
        self._record(key)
        super().write_bytes(key, data)

    def create_bytes(self, key: str, data: bytes) -> None:
        self._record(key)
        super().create_bytes(key, data)


@pytest.fixture
def exported(settings, documents, tmp_path) -> LocalStore:
    import_evidences(settings, documents)
    out = LocalStore(tmp_path / "out")
    export_all(LocalStore(tmp_path / "ws"), out)
    shutil.rmtree(tmp_path / "ws")
    return out


def test_import_all_escribe_raw_antes_que_manifiestos_y_estos_antes_que_el_resto(
    exported, tmp_path
):
    destination = RecordingStore(tmp_path / "ws")

    import_all(destination, exported, datetime.now(UTC))

    def phase(key: str) -> int:
        for index, prefix in enumerate(
            ("evidence/raw/", "evidence/manifests/", "evidence/reviews/")
        ):
            if key.startswith(prefix):
                return index
        return 3

    phases = [phase(key) for key in destination.order]
    assert phases == sorted(phases)
    assert {0, 1} <= set(phases)


def test_import_interrumpido_en_el_primer_manifiesto_deja_solo_huerfanos(
    settings, exported, tmp_path
):
    destination = RecordingStore(tmp_path / "ws", fail_prefix="evidence/manifests/")

    with pytest.raises(WorkspaceError):
        import_all(destination, exported, datetime.now(UTC))

    verify = ens(settings, "evidence", "verify", "--all")
    assert verify.exit_code == 1
    problems = [
        line for line in verify.output.splitlines() if line.strip().startswith(("-", "evidence/"))
    ]
    assert problems
    assert all("huérfano" in line for line in problems)
    assert "falta el artefacto" not in verify.output


def test_workspace_no_importa_evidence_ni_scope():
    package = Path(__file__).resolve().parents[2] / "src" / "ens_evidence" / "workspace"
    imported: set[str] = set()
    for path in package.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
                imported.update(f"{node.module}.{alias.name}" for alias in node.names)
            elif isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)

    forbidden = [
        name
        for name in imported
        if name.startswith(("ens_evidence.evidence", "ens_evidence.scope"))
    ]
    assert forbidden == []
