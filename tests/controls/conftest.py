import boto3
import pytest
import yaml
from moto import mock_aws
from typer.testing import CliRunner

from ens_evidence.cli import app

BUCKET = "evidencias"
runner = CliRunner()


def local_settings(tmp_path):
    path = tmp_path / "settings.yaml"
    path.write_text(
        yaml.safe_dump({"workspace": {"backend": "local", "local": {"path": str(tmp_path / "ws")}}})
    )
    return path


def s3_settings(tmp_path):
    path = tmp_path / "settings-s3.yaml"
    path.write_text(
        yaml.safe_dump({"workspace": {"backend": "s3", "s3": {"bucket": BUCKET, "prefix": "org"}}})
    )
    return path


@pytest.fixture
def ens(tmp_path):
    settings = local_settings(tmp_path)

    def run(*args: str, settings_file=settings):
        return runner.invoke(app, ["--settings", str(settings_file), *args])

    return run


@pytest.fixture
def s3(tmp_path, monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        boto3.client("s3").create_bucket(Bucket=BUCKET)
        yield s3_settings(tmp_path)
