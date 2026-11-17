import os

import pytest

from ens_evidence.workspace import AccessDenied, InvalidKey, LocalStore, NotFound


@pytest.fixture
def root(tmp_path):
    path = tmp_path / "ws"
    path.mkdir()
    return path


@pytest.fixture
def outside(tmp_path):
    path = tmp_path / "outside"
    path.mkdir()
    (path / "secret.txt").write_text("secreto", encoding="utf-8")
    return path


@pytest.fixture
def store(root):
    return LocalStore(root)


def test_escribe_y_lee_un_fichero(store):
    store.write_text("a/b.txt", "hola")

    assert store.read_text("a/b.txt") == "hola"
    assert store.exists("a/b.txt")


def test_leer_clave_inexistente_lanza_not_found(store):
    with pytest.raises(NotFound):
        store.read_text("nada.txt")


def test_exists_es_falso_para_clave_inexistente(store):
    assert not store.exists("nada.txt")


@pytest.mark.parametrize("key", ["../x", "/abs", ""])
def test_claves_invalidas_se_rechazan_en_todas_las_operaciones(store, key):
    for operation in (
        lambda: store.read_text(key),
        lambda: store.write_text(key, "x"),
        lambda: store.exists(key),
    ):
        with pytest.raises(InvalidKey):
            operation()


def test_listar_con_prefijo_invalido_se_rechaza(store):
    with pytest.raises(InvalidKey):
        store.list("../outside")


def test_lista_las_claves_ordenadas_bajo_un_prefijo(store):
    store.write_text("scope/b.txt", "1")
    store.write_text("scope/a/c.txt", "2")
    store.write_text("scope-otro/d.txt", "3")

    assert store.list("scope") == ["scope/a/c.txt", "scope/b.txt"]
    assert store.list() == ["scope-otro/d.txt", "scope/a/c.txt", "scope/b.txt"]


def test_symlink_a_fichero_externo_no_se_lee(root, outside, store):
    (root / "link.txt").symlink_to(outside / "secret.txt")

    with pytest.raises(AccessDenied):
        store.read_text("link.txt")


def test_symlink_a_fichero_externo_no_se_sobrescribe(root, outside, store):
    (root / "link.txt").symlink_to(outside / "secret.txt")

    with pytest.raises(AccessDenied):
        store.write_text("link.txt", "pwned")

    assert (outside / "secret.txt").read_text(encoding="utf-8") == "secreto"


def test_symlink_de_ancestro_hacia_fuera_se_rechaza_en_lectura_escritura_y_exists(
    root, outside, store
):
    (root / "dir").symlink_to(outside, target_is_directory=True)

    with pytest.raises(AccessDenied):
        store.read_text("dir/secret.txt")
    with pytest.raises(AccessDenied):
        store.write_text("dir/nuevo.txt", "pwned")
    with pytest.raises(AccessDenied):
        store.exists("dir/secret.txt")

    assert not (outside / "nuevo.txt").exists()


def test_listado_rechaza_symlink_que_apunta_fuera(root, outside, store):
    store.write_text("ok.txt", "x")
    (root / "dir").symlink_to(outside, target_is_directory=True)

    with pytest.raises(AccessDenied):
        store.list()


def test_listado_con_prefijo_que_es_symlink_externo_se_rechaza(root, outside, store):
    (root / "dir").symlink_to(outside, target_is_directory=True)

    with pytest.raises(AccessDenied):
        store.list("dir")


def test_fallo_al_reemplazar_no_altera_el_fichero_ni_deja_residuos(store, monkeypatch):
    store.write_text("a.txt", "original")

    def boom(*_args, **_kwargs):
        raise OSError("disco lleno")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(Exception):  # noqa: B017 - tipo concreto cubierto en otro test
        store.write_text("a.txt", "nuevo")
    monkeypatch.undo()

    assert store.read_text("a.txt") == "original"
    assert store.list() == ["a.txt"]


def test_fallo_a_mitad_de_escritura_no_crea_el_fichero_ni_aparece_en_list(store, monkeypatch):
    def boom(*_args, **_kwargs):
        raise OSError("fallo de fsync")

    monkeypatch.setattr(os, "fsync", boom)
    with pytest.raises(Exception):  # noqa: B017
        store.write_text("nuevo.txt", "parcial")
    monkeypatch.undo()

    assert not store.exists("nuevo.txt")
    assert store.list() == []
    assert [p.name for p in store._root.iterdir()] == []


def test_fallo_de_almacenamiento_se_traduce_a_error_tipado(store, monkeypatch):
    from ens_evidence.workspace import StorageUnavailable

    def boom(*_args, **_kwargs):
        raise OSError("io")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(StorageUnavailable):
        store.write_text("a.txt", "x")


def test_lectura_con_limite_devuelve_el_prefijo(store):
    store.write_bytes("a.bin", b"abcdef")

    assert store.read_bytes("a.bin", 3) == b"abc"
    assert store.read_bytes("a.bin", 6) == b"abcdef"
    assert store.read_bytes("a.bin", 0) == b""


def test_lectura_sin_limite_devuelve_todo_y_limite_mayor_tambien(store):
    store.write_bytes("a.bin", b"abcdef")

    assert store.read_bytes("a.bin") == b"abcdef"
    assert store.read_bytes("a.bin", 1000) == b"abcdef"


def test_lectura_con_limite_de_clave_inexistente_lanza_not_found(store):
    with pytest.raises(NotFound):
        store.read_bytes("nada.bin", 5)


def test_lectura_con_limite_no_carga_el_fichero_entero(store, monkeypatch):
    store.write_bytes("a.bin", b"x" * 1000)
    monkeypatch.setattr(
        "pathlib.Path.read_bytes", lambda self: (_ for _ in ()).throw(AssertionError("completo"))
    )

    assert store.read_bytes("a.bin", 10) == b"x" * 10
