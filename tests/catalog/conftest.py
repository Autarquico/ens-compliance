import hashlib
import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
CATALOG_DIR = REPO / "src" / "ens_evidence" / "catalog"
SOURCE_DIR = CATALOG_DIR / "source"


@pytest.fixture(scope="session")
def extractor():
    path = REPO / "tools" / "extract_catalog.py"
    spec = importlib.util.spec_from_file_location("extract_catalog", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def meta() -> dict:
    return yaml.safe_load((SOURCE_DIR / "anexo-ii.meta.yaml").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def snapshot(meta) -> bytes:
    return (SOURCE_DIR / meta["snapshot"]).read_bytes()


@pytest.fixture(scope="session")
def catalog():
    from ens_evidence.catalog import load_catalog

    return load_catalog()


@pytest.fixture
def extract_manipulated(extractor, snapshot, meta):
    """Extrae de una copia manipulada de la instantánea; devuelve los problemas reportados."""

    def run(manipulate) -> list[str]:
        html = manipulate(snapshot.decode("utf-8")).encode("utf-8")
        assert html != snapshot, "la manipulación no cambió la instantánea"
        altered = {**meta, "snapshot_sha256": hashlib.sha256(html).hexdigest()}
        with pytest.raises(extractor.ExtractionError) as failure:
            extractor.extract(html, altered)
        return failure.value.problems

    return run
