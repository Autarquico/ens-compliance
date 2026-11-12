import gzip
import hashlib
import io
import tarfile
from datetime import UTC, datetime

import pytest
import yaml
from pydantic import ValidationError

from ens_evidence.connectors.passwords.config import PasswordsConfig
from ens_evidence.kit.backups import BackupInputError
from ens_evidence.kit.passwords_setup import PasswordsInputs, render_bundle

NOW = datetime(2026, 11, 11, 10, 0, tzinfo=UTC)


def inputs(**over):
    base = {"asset_id": "vaultwarden", "inactive_days": 90, "host": "contrasenas.example.es"}
    return PasswordsInputs(**{**base, **over})


def members(blob: bytes) -> dict[str, bytes]:
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        return {m.name: tar.extractfile(m).read() for m in tar.getmembers()}


def test_mismas_entradas_y_fecha_dan_bytes_identicos():
    assert render_bundle(inputs(), NOW) == render_bundle(inputs(), NOW)
    assert gzip.decompress(render_bundle(inputs(), NOW))


def test_el_manifest_coincide_con_los_ficheros():
    files = members(render_bundle(inputs(), NOW))
    manifest = files.pop("contrasenas/MANIFEST").decode().splitlines()
    assert manifest[0] == "version: 1"
    listed = {name: digest for digest, name in (line.split("  ") for line in manifest[1:])}
    assert listed == {n: hashlib.sha256(b).hexdigest() for n, b in files.items()}
    assert sorted(files) == ["contrasenas/passwords.yaml"]


def test_la_configuracion_es_valida_para_el_conector_y_sin_secretos():
    body = members(render_bundle(inputs(), NOW))["contrasenas/passwords.yaml"]
    data = yaml.safe_load(body)

    assert data == {
        "asset_id": "vaultwarden",
        "export_path": "/run/kit/contrasenas/usuarios.json",
        "inactive_days": 90,
        "max_export_age_minutes": 60,
    }
    assert PasswordsConfig.model_validate(data)
    assert b"token" not in body.lower() and b"password" not in body.lower()


def test_el_host_se_normaliza():
    assert inputs(host=" Contrasenas.Example.ES ").host == "contrasenas.example.es"


@pytest.mark.parametrize(
    "over",
    [
        {"inactive_days": 0},
        {"inactive_days": "90"},
        {"inactive_days": True},
        {"asset_id": " "},
        {"host": "https://contrasenas.example.es"},
        {"host": "contrasenas.example.es:8443"},
        {"host": "contrasenas.example.es/x"},
        {"host": "localhost"},
        {"host": ""},
        {"extra": 1},
    ],
)
def test_entradas_no_validas_se_rechazan(over):
    with pytest.raises(ValidationError):
        inputs(**over)


def test_fecha_sin_zona_horaria_se_rechaza():
    with pytest.raises(BackupInputError):
        render_bundle(inputs(), datetime(2026, 11, 11))
