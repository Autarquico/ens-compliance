import pytest

from ens_evidence.workspace import AccessDenied, LocalStore


@pytest.fixture
def root(tmp_path):
    path = tmp_path / "ws"
    path.mkdir()
    return path


@pytest.fixture
def outside(tmp_path):
    path = tmp_path / "outside"
    path.mkdir()
    return path


def test_el_lock_crea_el_fichero_y_los_directorios_padre(root):
    with LocalStore(root).exclusive_lock("a/b/x.lock"):
        assert (root / "a" / "b" / "x.lock").is_file()


def test_un_directorio_padre_enlazado_fuera_se_rechaza_sin_crear_nada_fuera(root, outside):
    (root / "dir").symlink_to(outside, target_is_directory=True)

    with pytest.raises(AccessDenied), LocalStore(root).exclusive_lock("dir/sub/x.lock"):
        pytest.fail("no debe entrar")

    assert list(outside.iterdir()) == []


def test_un_fichero_de_lock_enlazado_fuera_se_rechaza(root, outside):
    (outside / "target").write_text("x")
    (root / "x.lock").symlink_to(outside / "target")

    with pytest.raises(AccessDenied), LocalStore(root).exclusive_lock("x.lock"):
        pytest.fail("no debe entrar")
