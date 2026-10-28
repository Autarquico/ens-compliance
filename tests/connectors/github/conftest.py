import boto3
import pytest
from moto import mock_aws

from ens_evidence.connectors import ConnectorRegistry
from ens_evidence.connectors.github import GitHubConnector, GitHubRun
from ens_evidence.connectors.github.connector import TOKEN_ENV
from ens_evidence.workspace import LocalStore, S3Store

from .cli_support import BUCKET, seed, write_settings
from .fake_api import NOW, ORG, TOKEN, FakeApi


@pytest.fixture
def api() -> FakeApi:
    return FakeApi()


@pytest.fixture
def config() -> GitHubRun:
    return GitHubRun(asset_id="gh-taller", organization=ORG, inventory_repos=["facturas", "infra"])


@pytest.fixture
def connector(api: FakeApi) -> GitHubConnector:
    return GitHubConnector(transport=api.transport, clock=lambda: NOW, env={TOKEN_ENV: TOKEN})


@pytest.fixture
def collect(connector, config):
    def run(**changes):
        return connector.collect(config.model_copy(update=changes))

    return run


@pytest.fixture
def section(collect):
    def get(name: str, **changes) -> dict:
        return collect(**changes).data["sections"][name]

    return get


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
    """Registra en la CLI un conector de GitHub construido con los argumentos dados."""

    def install(**kwargs) -> None:
        class Injected(GitHubConnector):
            def __init__(self) -> None:
                super().__init__(**kwargs)

        registry = ConnectorRegistry()
        registry.register(Injected)
        monkeypatch.setattr("ens_evidence.cli.connector_registry", lambda: registry)

    return install
