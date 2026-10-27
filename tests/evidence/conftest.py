import boto3
import pytest
from moto import mock_aws

from ens_evidence.workspace import LocalStore, S3Store

BUCKET = "evidencias"


@pytest.fixture(params=["local", "s3"])
def store(request, tmp_path, monkeypatch):
    if request.param == "local":
        yield LocalStore(tmp_path / "ws")
        return
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        boto3.client("s3").create_bucket(Bucket=BUCKET)
        yield S3Store(BUCKET, "org")
