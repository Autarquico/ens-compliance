"""`ens-evidence endpoint script --out DIR`."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from ens_evidence.cli import app
from ens_evidence.endpoint.bundle import BundleTargetError, write_bundle
from ens_evidence.endpoint.pack import SCRIPT_NAME, bundle_files

runner = CliRunner()


def tree(root: Path) -> set[Path]:
    return {path.relative_to(root) for path in root.rglob("*")}


def test_escribe_el_script_y_el_paquete_solo_en_el_directorio_indicado(tmp_path, monkeypatch):
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    out = tmp_path / "entrega"

    result = runner.invoke(app, ["endpoint", "script", "--out", str(out)])

    assert result.exit_code == 0, result.output
    assert {path.as_posix() for path in tree(out) if (out / path).is_file()} == set(bundle_files())
    assert {path for path in tree(tmp_path) if path.parts[0] not in ("cwd", "entrega")} == set()
    assert tree(cwd) == set()
    assert (out / SCRIPT_NAME).stat().st_mode & 0o111


def test_un_directorio_con_contenido_se_rechaza_sin_tocarlo(tmp_path):
    out = tmp_path / "entrega"
    out.mkdir()
    (out / "mio.txt").write_text("importante")

    result = runner.invoke(app, ["endpoint", "script", "--out", str(out)])

    assert result.exit_code == 2
    assert tree(out) == {Path("mio.txt")}
    assert (out / "mio.txt").read_text() == "importante"


def test_un_fichero_o_un_enlace_como_destino_se_rechaza(tmp_path):
    file_target = tmp_path / "fichero"
    file_target.write_text("x")
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "enlace"
    link.symlink_to(real)

    for target in (file_target, link):
        result = runner.invoke(app, ["endpoint", "script", "--out", str(target)])
        assert result.exit_code == 2, target
    assert tree(real) == set()


@pytest.mark.parametrize("planted", ["collect-endpoint.sh", "queries"])
def test_un_enlace_plantado_durante_la_escritura_no_se_sigue(tmp_path, monkeypatch, planted):
    out = tmp_path / "entrega"
    victim = tmp_path / "victima"
    victim.mkdir() if planted == "queries" else victim.write_text("intacta")
    files = bundle_files()

    class Planting(dict):
        def items(self):
            (out / planted).symlink_to(victim)
            return super().items()

    monkeypatch.setattr("ens_evidence.endpoint.bundle.bundle_files", lambda: Planting(files))

    with pytest.raises(BundleTargetError):
        write_bundle(out)

    assert (victim.read_text() == "intacta") if victim.is_file() else not list(victim.iterdir())


def test_un_directorio_vacio_preexistente_se_rellena(tmp_path):
    out = tmp_path / "entrega"
    out.mkdir(mode=0o755)

    assert len(write_bundle(out)) == len(bundle_files())


def test_un_directorio_vacio_escribible_por_otros_se_rechaza(tmp_path):
    out = tmp_path / "entrega"
    out.mkdir()
    out.chmod(0o777)

    with pytest.raises(BundleTargetError, match="escribible"):
        write_bundle(out)

    assert tree(out) == set()


def test_un_directorio_de_otro_usuario_se_rechaza(tmp_path, monkeypatch):
    out = tmp_path / "entrega"
    out.mkdir(mode=0o755)
    monkeypatch.setattr("os.geteuid", lambda: out.stat().st_uid + 1)

    with pytest.raises(BundleTargetError, match="otro usuario"):
        write_bundle(out)

    assert tree(out) == set()


def test_un_destino_cambiado_por_un_enlace_tras_comprobarlo_no_se_sigue(tmp_path, monkeypatch):
    out = tmp_path / "entrega"
    victim = tmp_path / "victima"
    victim.mkdir()
    real_mkdir = Path.mkdir

    def swap_for_link(self, *args, **kwargs):
        if self == out:
            out.symlink_to(victim)
        return real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", swap_for_link)

    with pytest.raises(BundleTargetError):
        write_bundle(out)

    assert tree(victim) == set()


def test_sin_permiso_de_escritura_el_error_lo_dice(tmp_path):
    parent = tmp_path / "solo-lectura"
    parent.mkdir()
    parent.chmod(0o555)

    try:
        with pytest.raises(BundleTargetError, match="permiso"):
            write_bundle(parent / "entrega")
    finally:
        parent.chmod(0o755)


def test_un_error_de_disco_llega_a_la_cli_con_su_propio_mensaje(tmp_path, monkeypatch):
    def full_disk(*_args, **_kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("ens_evidence.endpoint.bundle._create_exclusive", full_disk)

    result = runner.invoke(app, ["endpoint", "script", "--out", str(tmp_path / "entrega")])

    assert result.exit_code == 2
    assert "Error al escribir ficheros" in result.output
