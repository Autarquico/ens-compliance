import io
from datetime import UTC, datetime

import boto3
import pytest
import yaml
from botocore.exceptions import ClientError
from moto import mock_aws

from ens_evidence.backup import verify_immutability
from ens_evidence.backup.immutability import PROTECTED_PREFIXES

from .conftest import BACKUP_KEY, BACKUP_SECRET, ens

BUCKET = "copias"
REPOSITORY = f"s3:https://s3.amazonaws.com/{BUCKET}/repo"
EXISTING = {
    "repo/config": b"config de restic",
    "repo/data/ab/abcdef": b"datos reales",
    "repo/index/i1": b"indice",
    "repo/snapshots/s1": b"snapshot",
    "repo/keys/k1": b"clave",
    "repo/locks/l1": b"bloqueo",
}


@pytest.fixture
def bucket(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        client = boto3.client("s3")
        client.create_bucket(Bucket=BUCKET)
        for key, body in EXISTING.items():
            client.put_object(Bucket=BUCKET, Key=key, Body=body)
        yield client


def snapshot_of(client) -> dict[str, tuple[bytes, str]]:
    listed = client.list_objects_v2(Bucket=BUCKET).get("Contents", [])
    return {
        o["Key"]: (client.get_object(Bucket=BUCKET, Key=o["Key"])["Body"].read(), o["ETag"])
        for o in listed
        if "ens-canary-" not in o["Key"]
    }


def test_destino_que_permite_borrar_informa_fallo_en_cada_prefijo(bucket):
    check = verify_immutability(bucket, REPOSITORY, BUCKET, "repo", datetime.now(UTC))

    assert not check.protected
    assert [r.prefix for r in check.results] == list(PROTECTED_PREFIXES)
    assert not any(r.protected for r in check.results)


def test_verify_immutability_solo_toca_claves_canario(bucket):
    before = snapshot_of(bucket)

    verify_immutability(bucket, REPOSITORY, BUCKET, "repo", datetime.now(UTC))

    assert snapshot_of(bucket) == before
    assert set(before) == set(EXISTING)


class ImmutableBucket:
    """Frontera de red: un destino con bloqueo que rechaza borrar y sobrescribir."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_object(self, Bucket, Key, Body):  # noqa: N803
        if Key in self.objects:
            raise ClientError({"Error": {"Code": "AccessDenied"}}, "PutObject")
        self.objects[Key] = Body

    def delete_object(self, Bucket, Key):  # noqa: N803
        raise ClientError({"Error": {"Code": "AccessDenied"}}, "DeleteObject")

    def get_object(self, Bucket, Key):  # noqa: N803
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": io.BytesIO(self.objects[Key])}


def test_destino_inmutable_se_informa_como_protegido():
    check = verify_immutability(ImmutableBucket(), REPOSITORY, BUCKET, "repo", datetime.now(UTC))

    assert check.protected


def test_cli_informa_fallo_registra_la_prueba_y_sale_con_1(bucket, tmp_path, monkeypatch):
    path = tmp_path / "settings.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "workspace": {"backend": "local", "local": {"path": str(tmp_path / "ws")}},
                "backup": {"restic_repository": REPOSITORY},
            }
        )
    )
    monkeypatch.setenv("ENS_BACKUP_RESTIC_PASSWORD", "pw")
    monkeypatch.setenv("ENS_BACKUP_AWS_ACCESS_KEY_ID", BACKUP_KEY)
    monkeypatch.setenv("ENS_BACKUP_AWS_SECRET_ACCESS_KEY", BACKUP_SECRET)
    before = snapshot_of(bucket)

    result = ens(path, "backup", "verify-immutability")

    assert result.exit_code == 1
    assert "NO protegido" in result.output
    assert BACKUP_SECRET not in result.output
    recorded = list((tmp_path / "ws" / "evidence" / "backups" / "immutability").glob("*.yaml"))
    assert len(recorded) == 1
    assert snapshot_of(bucket) == before
