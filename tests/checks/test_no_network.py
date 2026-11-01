"""El motor no tiene red: ni sockets, ni procesos, ni conectores, ni entry points."""

import importlib.metadata
import socket
import subprocess

import pytest

from ens_evidence.checks import engine
from ens_evidence.checks.engine import OutcomeKind, run_all
from ens_evidence.connectors import registry as connector_registry
from ens_evidence.connectors.base import Connector
from ens_evidence.connectors.cloudflare.connector import CloudflareConnector
from ens_evidence.connectors.github.connector import GitHubConnector
from ens_evidence.connectors.google_workspace.connector import GoogleWorkspaceConnector
from ens_evidence.connectors.restic.connector import ResticConnector

from .test_engine import NOW, collect, endpoint_store, import_host, store  # noqa: F401

CONNECTORS = (GitHubConnector, CloudflareConnector, GoogleWorkspaceConnector, ResticConnector)


def forbidden(what):
    def fail(*_args, **_kwargs):
        raise AssertionError(f"el motor no puede usar {what}")

    return fail


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", forbidden("socket.connect"))
    monkeypatch.setattr(socket, "create_connection", forbidden("create_connection"))
    monkeypatch.setattr(subprocess, "run", forbidden("subprocess.run"))
    monkeypatch.setattr(subprocess, "Popen", forbidden("subprocess.Popen"))
    monkeypatch.setattr(importlib.metadata, "entry_points", forbidden("entry_points"))
    monkeypatch.setattr(connector_registry, "entry_points", forbidden("entry_points"))
    monkeypatch.setattr(
        connector_registry.ConnectorRegistry, "load_entry_points", forbidden("load_entry_points")
    )
    for connector in (Connector, *CONNECTORS):
        for method in ("__init__", "collect", "prepare", "from_settings"):
            if method in vars(connector):
                monkeypatch.setattr(connector, method, forbidden(f"{connector.__name__}.{method}"))


def test_the_guards_do_fire(offline):
    with pytest.raises(AssertionError):
        socket.socket().connect(("127.0.0.1", 9))
    with pytest.raises(AssertionError):
        subprocess.run(["true"], check=False)
    with pytest.raises(AssertionError):
        importlib.metadata.entry_points(group="ens_evidence.connectors")
    with pytest.raises(AssertionError):
        GitHubConnector()


def test_run_all_over_collected_evidence_needs_no_network(offline, endpoint_store):  # noqa: F811
    collect(endpoint_store)
    import_host(endpoint_store)

    outcomes = {o.connector: o for o in run_all(endpoint_store, NOW)}

    assert outcomes["github"].kind is OutcomeKind.RECORDED
    assert outcomes["endpoint"].kind is OutcomeKind.RECORDED
    assert outcomes["github"].run is not None and outcomes["github"].run.complete
    assert outcomes["endpoint"].run is not None and outcomes["endpoint"].run.complete


def test_run_all_without_any_evidence_needs_no_network_either(offline, store):  # noqa: F811
    outcomes = run_all(store, NOW)

    assert [o.kind for o in outcomes if o.connector == "github"] == [OutcomeKind.RECORDED]


def test_engine_module_does_not_expose_connectors():
    assert not any(
        isinstance(value, type) and issubclass(value, Connector) for value in vars(engine).values()
    )
