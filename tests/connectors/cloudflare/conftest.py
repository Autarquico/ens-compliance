import boto3
import pytest
from moto import mock_aws

from ens_evidence.connectors import ConnectorRegistry
from ens_evidence.connectors.cloudflare import CloudflareConnector, CloudflareRun
from ens_evidence.connectors.cloudflare.connector import TOKEN_ENV
from ens_evidence.workspace import LocalStore, S3Store

from .cli_support import BUCKET, seed, write_settings
from .fake_api import ACCOUNT, NOW, TOKEN, FakeApi


@pytest.fixture
def api() -> FakeApi:
    return FakeApi()


@pytest.fixture
def config() -> CloudflareRun:
    return CloudflareRun(
        asset_id="cf-taller", account_id=ACCOUNT, inventory_zones=["taller-norte.example"]
    )


@pytest.fixture
def connector(api: FakeApi) -> CloudflareConnector:
    return CloudflareConnector(transport=api.transport, clock=lambda: NOW, env={TOKEN_ENV: TOKEN})


@pytest.fixture
def collect(connector, config):
    def run(**changes):
        return connector.collect(config.model_copy(update=changes))

    return run


@pytest.fixture(params=["local", "s3"])
def workspace(request, tmp_path, monkeypatch):
    """(settings, store) del mismo workspace con el alcance de ejemplo y la configuración."""
    if request.param == "local":
        settings = {"backend": "local", "local": {"path": str(tmp_path / "ws")}}
        store = LocalStore(tmp_path / "ws")
        yield write_settings(tmp_path, settings), seed(store)
        return
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        boto3.client("s3").create_bucket(Bucket=BUCKET)
        settings = {"backend": "s3", "s3": {"bucket": BUCKET, "prefix": "org"}}
        yield write_settings(tmp_path, settings), seed(S3Store(BUCKET, "org"))


@pytest.fixture
def install_connector(monkeypatch):
    """Registra en la CLI un conector de Cloudflare construido con los argumentos dados."""

    def install(**kwargs) -> None:
        class Injected(CloudflareConnector):
            def __init__(self) -> None:
                super().__init__(**kwargs)

        registry = ConnectorRegistry()
        registry.register(Injected)
        monkeypatch.setattr("ens_evidence.cli.connector_registry", lambda: registry)

    return install
