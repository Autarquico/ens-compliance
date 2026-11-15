import gzip
import hashlib
import io
import json
import tarfile
from datetime import UTC, datetime

import pytest
import yaml
from pydantic import ValidationError

from ens_evidence.kit.backups import BackupInputError
from ens_evidence.kit.keystore import KeystoreInputs, openbao_file, render_bundle

NOW = datetime(2026, 11, 10, 10, 0, tzinfo=UTC)


def inputs(**over):
    base = {"asset_id": "openbao-caja", "key_shares": 5, "key_threshold": 3, "max_key_age_days": 90}
    return KeystoreInputs(**{**base, **over})


def members(blob: bytes) -> dict[str, bytes]:
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        return {m.name: tar.extractfile(m).read() for m in tar.getmembers()}


def test_mismas_entradas_y_fecha_dan_bytes_identicos():
    assert render_bundle(inputs(), NOW) == render_bundle(inputs(), NOW)
    assert gzip.decompress(render_bundle(inputs(), NOW))


def test_el_manifest_coincide_con_los_ficheros():
    files = members(render_bundle(inputs(), NOW))
    manifest = files.pop("claves/MANIFEST").decode().splitlines()
    assert manifest[0] == "version: 1"
    listed = {name: digest for digest, name in (line.split("  ") for line in manifest[1:])}
    assert listed == {n: hashlib.sha256(b).hexdigest() for n, b in files.items()}
    assert sorted(files) == ["claves/init.json", "claves/keystore.yaml", "claves/openbao.hcl"]


def test_contenido_del_paquete():
    files = members(render_bundle(inputs(), NOW))
    assert json.loads(files["claves/init.json"]) == {"key_shares": 5, "key_threshold": 3}
    assert yaml.safe_load(files["claves/keystore.yaml"]) == {
        "asset_id": "openbao-caja",
        "address": "http://openbao:8200",
        "max_key_age_days": 90,
        "max_token_period_hours": 72,
    }
    assert files["claves/openbao.hcl"] == openbao_file("config.hcl")


@pytest.mark.parametrize(
    "over",
    [
        {"key_shares": 3, "key_threshold": 4},
        {"key_shares": 5, "key_threshold": 1},
        {"key_shares": 2, "key_threshold": 2},
        {"key_shares": 11, "key_threshold": 3},
        {"max_key_age_days": 0},
        {"asset_id": " "},
        {"key_threshold": "3"},
        {"extra": 1},
    ],
)
def test_entradas_no_validas_se_rechazan(over):
    with pytest.raises(ValidationError):
        inputs(**over)


def test_umbral_igual_al_numero_de_partes_es_valido():
    assert inputs(key_shares=3, key_threshold=3).key_threshold == 3


def test_fecha_sin_zona_horaria_se_rechaza():
    with pytest.raises(BackupInputError):
        render_bundle(inputs(), datetime(2026, 11, 10))
