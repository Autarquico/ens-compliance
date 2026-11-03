"""El almacén de lectura de una operación: una lectura por clave y nunca escribe."""

import pytest

from ens_evidence.assessment.dossier.readonly import ReadOnlyStore
from ens_evidence.workspace import LocalStore, NotFound


class Counting(LocalStore):
    def __init__(self, root):
        super().__init__(root)
        self.reads: list[str] = []

    def read_bytes(self, key):
        self.reads.append(key)
        return super().read_bytes(key)


def test_cada_clave_se_lee_una_vez_y_el_cambio_posterior_no_se_ve(tmp_path):
    inner = Counting(tmp_path)
    inner.write_text("a.txt", "uno")
    store = ReadOnlyStore(inner)

    assert store.read_bytes("a.txt") == b"uno"
    inner.write_text("a.txt", "dos")
    assert store.read_bytes("a.txt") == b"uno"
    assert inner.reads == ["a.txt"]


def test_la_ausencia_tambien_se_memoriza(tmp_path):
    store = ReadOnlyStore(LocalStore(tmp_path))

    for _ in range(2):
        with pytest.raises(NotFound):
            store.read_text("falta.txt")
    assert store.exists("falta.txt") is False


def test_escribir_es_un_error(tmp_path):
    store = ReadOnlyStore(LocalStore(tmp_path))

    for write in (store.write_text, store.write_bytes, store.create_bytes):
        with pytest.raises(TypeError):
            write("a", "x" if write is store.write_text else b"x")
