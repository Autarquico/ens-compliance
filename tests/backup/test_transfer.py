import hashlib
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from ens_evidence.backup.transfer import export_all, import_all
from ens_evidence.cli import app
from ens_evidence.models.enums import Role
from ens_evidence.scope import (
    APPROVAL_KEY,
    ScopeState,
    approve_scope,
    revoke_approval,
    scope_status,
)
from ens_evidence.workspace import LocalStore

EXAMPLE_SCOPE = Path(__file__).resolve().parents[2] / "examples" / "workspace" / "scope"
APPROVED_AT = datetime(2026, 10, 27, 9, 0, tzinfo=UTC)
runner = CliRunner()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def tree(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): sha(p.read_bytes()) for p in root.rglob("*") if p.is_file()
    }


CONTENTS = {
    "scope/assets.yaml": b"assets: []\n",
    "evidence/raw/" + "a" * 64: bytes(range(256)) * 8,
    "evidence/manifests/EV-1.yaml": "evidencia: ñandú\n".encode(),
    "evidence/backups/BK-1.yaml": b"status: completed\n",
    "otros/fuera-de-synced.bin": b"\x00\xff\x00",
    "vacio": b"",
}


def fill(store, contents=CONTENTS) -> None:
    for key, data in contents.items():
        store.write_bytes(key, data)


def test_export_de_workspace_s3_copia_todas_las_claves_por_bytes(s3, tmp_path):
    fill(s3)

    keys = export_all(s3, LocalStore(tmp_path / "out"))

    assert sorted(keys) == sorted(CONTENTS)
    assert tree(tmp_path / "out") == {key: sha(data) for key, data in CONTENTS.items()}


class MutatingStore(LocalStore):
    """Frontera de almacenamiento: otro proceso escribe mientras se exporta."""

    def __init__(self, root: Path, on_read) -> None:
        super().__init__(root)
        self._on_read = on_read
        self._fired = False

    def read_bytes(self, key: str) -> bytes:
        data = super().read_bytes(key)
        if not self._fired:
            self._fired = True
            self._on_read(self, key)
        return data


def settings_for(tmp_path: Path) -> Path:
    path = tmp_path / "settings.yaml"
    path.write_text(
        yaml.safe_dump({"workspace": {"backend": "local", "local": {"path": str(tmp_path / "ws")}}})
    )
    return path


def test_export_aborta_si_el_listado_cambia(tmp_path):
    source = MutatingStore(tmp_path / "ws", lambda s, key: s.write_bytes("nueva", b"x"))
    fill(source)

    with pytest.raises(Exception, match="cambió durante la exportación"):
        export_all(source, LocalStore(tmp_path / "out"))


def test_export_aborta_si_una_clave_cambia_de_contenido_durante_la_copia(tmp_path):
    source = MutatingStore(tmp_path / "ws", lambda s, key: s.write_bytes(key, b"cambiado"))
    fill(source)

    with pytest.raises(Exception, match="cambió durante la exportación"):
        export_all(source, LocalStore(tmp_path / "out"))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda s, key: s.write_bytes("nueva", b"x"),
        lambda s, key: s.write_bytes(key, b"cambiado"),
    ],
    ids=["listado", "contenido"],
)
def test_export_cli_abortado_no_deja_la_exportacion(tmp_path, monkeypatch, mutate):
    source = MutatingStore(tmp_path / "ws", mutate)
    fill(source)
    monkeypatch.setattr("ens_evidence.cli.open_workspace", lambda ctx: source)
    out = tmp_path / "out"

    result = runner.invoke(
        app, ["--settings", str(settings_for(tmp_path)), "workspace", "export", "--all", str(out)]
    )

    assert result.exit_code != 0
    assert "cambió durante la exportación" in result.output
    assert not out.exists()


def test_export_a_carpeta_destino_no_vacia_es_error_y_no_la_toca(tmp_path):
    fill(LocalStore(tmp_path / "ws"))
    out = tmp_path / "out"
    out.mkdir()
    (out / "previo.txt").write_text("previo")

    result = runner.invoke(
        app, ["--settings", str(settings_for(tmp_path)), "workspace", "export", "--all", str(out)]
    )

    assert result.exit_code != 0
    assert tree(out) == {"previo.txt": sha(b"previo")}


def test_export_e_import_roundtrip_conserva_los_bytes(tmp_path):
    source = LocalStore(tmp_path / "ws")
    fill(source)
    export_all(source, LocalStore(tmp_path / "out"))
    destination = LocalStore(tmp_path / "dest")

    report = import_all(destination, LocalStore(tmp_path / "out"), datetime.now(UTC))

    assert report.written == len(CONTENTS)
    assert tree(tmp_path / "dest") == tree(tmp_path / "ws")


def approved_workspace(root: Path) -> LocalStore:
    store = LocalStore(root)
    shutil.copytree(EXAMPLE_SCOPE, root / "scope")
    approve_scope(store, "Ana Ruiz", Role.SYSTEM_OWNER, APPROVED_AT)
    return store


def test_import_all_revoca_la_aprobacion_si_el_alcance_importado_es_distinto(tmp_path):
    destination = approved_workspace(tmp_path / "destino")
    export = tmp_path / "export"
    changed = LocalStore(tmp_path / "otro")
    shutil.copytree(EXAMPLE_SCOPE, tmp_path / "otro" / "scope")
    text = changed.read_text("scope/system.yaml")
    changed.write_text("scope/system.yaml", text + "\n# cambio de alcance\n")
    export_all(changed, LocalStore(export))
    assert scope_status(destination).state is ScopeState.APPROVED

    report = import_all(destination, LocalStore(export), datetime.now(UTC))

    assert report.approval_revoked
    assert scope_status(destination).state is not ScopeState.APPROVED


def test_import_all_de_claves_ajenas_al_alcance_conserva_la_aprobacion(tmp_path):
    destination = approved_workspace(tmp_path / "destino")
    export = tmp_path / "export"
    other = LocalStore(tmp_path / "otro")
    shutil.copytree(EXAMPLE_SCOPE, tmp_path / "otro" / "scope")
    other.write_text("org/answers.yaml", "organizacion: Taller Norte\n")
    export_all(other, LocalStore(export))

    report = import_all(destination, LocalStore(export), datetime.now(UTC))

    assert not report.approval_revoked
    assert scope_status(destination).state is ScopeState.APPROVED


def test_import_all_no_resucita_una_aprobacion_revocada_del_destino(tmp_path):
    origin = approved_workspace(tmp_path / "origen")
    export_all(origin, LocalStore(tmp_path / "export"))
    destination = approved_workspace(tmp_path / "destino")

    revoke_approval(destination, "revisión", datetime.now(UTC))
    revoked = destination.read_bytes(APPROVAL_KEY)

    import_all(destination, LocalStore(tmp_path / "export"), datetime.now(UTC))

    assert destination.read_bytes(APPROVAL_KEY) == revoked
    assert scope_status(destination).state is not ScopeState.APPROVED


def test_import_all_no_pisa_una_evidencia_existente_con_contenido_distinto(tmp_path):

    source = LocalStore(tmp_path / "src")
    source.write_bytes("evidence/manifests/EV-1.yaml", b"copia\n")
    source.write_bytes("otra", b"x")
    destination = LocalStore(tmp_path / "dest")
    destination.write_bytes("evidence/manifests/EV-1.yaml", b"original\n")

    with pytest.raises(Exception, match="contenido distinto"):
        import_all(destination, source, datetime.now(UTC))

    assert destination.read_bytes("evidence/manifests/EV-1.yaml") == b"original\n"
    assert not destination.exists("otra")
