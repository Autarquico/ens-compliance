import os

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


def _swap_parent_for_link(monkeypatch, root, outside, name):
    """Antes de abrir el componente `name` lo sustituye por un enlace hacia fuera (la carrera)."""
    real_open = os.open
    swapped = []

    def racing_open(path, flags, *args, **kwargs):
        if path == name and kwargs.get("dir_fd") is not None and not swapped:
            swapped.append(path)
            target = root / "a" / name
            target.rename(root / "a" / f"{name}-moved")
            target.symlink_to(outside, target_is_directory=True)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", racing_open)
    return swapped


@pytest.mark.parametrize("operation", ["lock", "write", "create"])
def test_un_padre_sustituido_por_un_enlace_entre_pasos_se_rechaza(
    monkeypatch, root, outside, operation
):
    (root / "a" / "b").mkdir(parents=True)
    store = LocalStore(root)
    swapped = _swap_parent_for_link(monkeypatch, root, outside, "b")

    with pytest.raises(AccessDenied):
        if operation == "lock":
            with store.exclusive_lock("a/b/c/x.lock"):
                pytest.fail("no debe entrar")
        elif operation == "write":
            store.write_bytes("a/b/c/x.txt", b"pwned")
        else:
            store.create_bytes("a/b/c/x.txt", b"pwned")

    assert swapped == ["b"]
    assert list(outside.iterdir()) == []
