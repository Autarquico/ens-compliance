import logging

import boto3
import pytest
from moto import mock_aws

from ens_evidence.connectors import ConnectorRegistry
from ens_evidence.connectors.google_workspace import (
    KEY_ENV,
    GoogleWorkspaceConfig,
    GoogleWorkspaceConnector,
)
from ens_evidence.workspace import LocalStore, S3Store

from .cli_support import BUCKET, seed, write_settings
from .credentials import key_json
from .fake_api import ADMIN_SUBJECT, DOMAIN, NOW, FakeApi


@pytest.fixture
def api() -> FakeApi:
    return FakeApi()


@pytest.fixture
def config() -> GoogleWorkspaceConfig:
    return GoogleWorkspaceConfig(
        asset_id="ws-identidad", admin_subject=ADMIN_SUBJECT, domains=[DOMAIN]
    )


@pytest.fixture
def connector(api: FakeApi) -> GoogleWorkspaceConnector:
    return GoogleWorkspaceConnector(
        transport=api.transport, clock=lambda: NOW, env={KEY_ENV: key_json()}
    )


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


@pytest.fixture
def debug_logging(caplog):
    caplog.set_level(logging.DEBUG)
    for name in ("httpx", "httpcore", "httpcore.http11", "httpcore.connection"):
        logging.getLogger(name).setLevel(logging.DEBUG)
    return caplog


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
    """Registra en la CLI un conector de Google Workspace construido con los argumentos dados."""

    def install(**kwargs) -> None:
        class Injected(GoogleWorkspaceConnector):
            def __init__(self) -> None:
                super().__init__(**kwargs)

        registry = ConnectorRegistry()
        registry.register(Injected)
        monkeypatch.setattr("ens_evidence.cli.connector_registry", lambda: registry)

    return install
