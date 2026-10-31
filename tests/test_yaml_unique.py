import pytest
import yaml

from ens_evidence.scope.inventory import load_assets
from ens_evidence.workspace import LocalStore
from ens_evidence.yaml_unique import safe_load_unique


@pytest.mark.parametrize(
    "text",
    [
        "assessment: NON_COMPLIANT\nassessment: COMPLIANT\n",
        "review:\n  by: Ana\n  by: Luis\n",
        "items:\n  - id: a\n    id: b\n",
    ],
    ids=["raiz", "anidada", "en-lista"],
)
def test_una_clave_repetida_no_se_lee(text):
    with pytest.raises(yaml.YAMLError, match="clave duplicada"):
        safe_load_unique(text)


def test_sin_claves_repetidas_lee_igual_que_safe_load():
    text = "a: 1\nb:\n  c: [1, 2]\n"

    assert safe_load_unique(text) == yaml.safe_load(text)


def test_el_inventario_con_una_clave_repetida_no_se_carga(tmp_path):
    store = LocalStore(tmp_path)
    store.write_text("scope/assets.yaml", "assets: []\nassets:\n  - id: portatil-1\n")

    assets, errors = load_assets(store)

    assert assets is None
    assert errors == ["scope/assets.yaml: no es YAML válido; revisa la sintaxis"]
