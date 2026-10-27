import pytest

from ens_evidence.workspace import InvalidKey, normalize_key


@pytest.mark.parametrize(
    "key",
    ["/etc/passwd", "../x", "a/../../x", "a/..", "", "///", ".", "a\\b", "a\x00b", "C:/x", "C:"],
)
def test_clave_no_valida_se_rechaza(key):
    with pytest.raises(InvalidKey):
        normalize_key(key)


def test_clave_con_separadores_redundantes_se_normaliza():
    assert normalize_key("a//b/./c") == "a/b/c"


def test_clave_vacia_solo_se_acepta_si_se_permite():
    assert normalize_key("", allow_empty=True) == ""
