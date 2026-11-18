import os

import pytest

from ens_evidence.workspace import AccessDenied, LocalStore, StorageUnavailable


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


# --- la raíz se abre una sola vez ----------------------------------------------------------------


def _swap_root_for_link(root, outside):
    root.rename(root.with_name("ws-moved"))
    root.symlink_to(outside, target_is_directory=True)


@pytest.mark.parametrize("operation", ["lock", "write", "create"])
def test_una_raiz_sustituida_por_un_enlace_tras_construir_no_saca_nada_del_workspace(
    root, outside, operation
):
    with LocalStore(root) as store:
        _swap_root_for_link(root, outside)

        if operation == "lock":
            with store.exclusive_lock("a/x.lock"):
                pass
        elif operation == "write":
            store.write_bytes("a/x.txt", b"data")
        else:
            store.create_bytes("a/x.txt", b"data")

    assert list(outside.iterdir()) == []
    assert (root.with_name("ws-moved") / "a").is_dir()


def test_una_raiz_que_es_un_enlace_al_construir_funciona(tmp_path, outside):
    link = tmp_path / "srv-ws"
    link.symlink_to(outside, target_is_directory=True)

    with LocalStore(link) as store:
        store.write_bytes("a/x.txt", b"data")
        assert store.read_bytes("a/x.txt") == b"data"

    assert (outside / "a" / "x.txt").read_bytes() == b"data"


def test_tras_cerrar_el_almacen_las_escrituras_fallan_cerradas(root):
    store = LocalStore(root)
    store.close()
    store.close()

    with pytest.raises(StorageUnavailable):
        store.write_bytes("x.txt", b"data")


@pytest.mark.parametrize("operation", ["lock", "write", "create"])
def test_una_raiz_ausente_que_aparece_como_enlace_antes_de_crearse_se_rechaza(
    tmp_path, outside, operation
):
    root = tmp_path / "ws"
    store = LocalStore(root)
    root.symlink_to(outside, target_is_directory=True)

    with pytest.raises(AccessDenied):
        if operation == "lock":
            with store.exclusive_lock("a/x.lock"):
                pass
        elif operation == "write":
            store.write_bytes("a/x.txt", b"data")
        else:
            store.create_bytes("a/x.txt", b"data")

    assert list(outside.iterdir()) == []


def test_una_raiz_ausente_se_crea_en_la_primera_escritura_y_no_al_leer(tmp_path):
    root = tmp_path / "nuevo" / "ws"
    store = LocalStore(root)
    assert store.list("") == []
    assert not root.exists()

    store.write_bytes("a/x.txt", b"data")

    assert (root / "a" / "x.txt").read_bytes() == b"data"
