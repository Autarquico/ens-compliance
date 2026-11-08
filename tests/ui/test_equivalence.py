"""Escenario «alcance y categorización equivalentes a la CLI»: mismo guion, mismo reloj."""

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

import ens_evidence.cli as cli
from ens_evidence.catalog.model import Dimension
from ens_evidence.categorization import CategorizationState, categorization_status
from ens_evidence.scope import ScopeState, scope_status
from ens_evidence.workspace import LocalStore

from .conftest import NOW, Session, snapshot_of, ui_client

JUSTIFICATION = "Criterio 1.º del anexo I, 3"
LEVELS = {"datos": "ALTO", "svc-facturacion": "MEDIO", "svc-soporte": "BAJO"}
ELEMENTS = [
    ("information", "datos"),
    ("service", "svc-facturacion"),
    ("service", "svc-soporte"),
]
runner = CliRunner()


def cli_script(ens) -> None:
    ens("scope", "set-system", "--name", "Sistema de prueba", "--description", "Para comparar")
    ens("scope", "add-asset", "--id", "lap-nuevo", "--type", "laptop", "--set", "os=macOS",
        "--set", "owner=Ana Ruiz", "--set", "encrypted=true", "--set", "usage=Trabajo",
        "--include")  # fmt: skip
    ens("scope", "add-service", "--id", "svc-soporte", "--name", "Soporte",
        "--asset", "lap-nuevo", "--asset", "srv-app", "--include")  # fmt: skip
    ens("scope", "add-dependency", "--id", "dep-nuevo", "--asset", "lap-nuevo",
        "--depends-on", "srv-app", "--purpose", "Acceso por SSH")  # fmt: skip
    ens("scope", "approve", "--by", "Ana Ruiz")
    ens("categorization", "init")
    ens("categorization", "add-information", "--id", "datos", "--description", "Datos de clientes")
    for kind, element in ELEMENTS:
        for dimension in Dimension:
            ens("categorization", "rate", "--id", element, "--dimension", dimension.value,
                "--level", LEVELS[element], "--justification", JUSTIFICATION,
                *(("--service",) if kind == "service" else ()))  # fmt: skip
    for kind, element in ELEMENTS:
        ens("categorization", "sign-valuation", "--id", element, "--by", "Ana Ruiz",
            *(("--service",) if kind == "service" else ()))  # fmt: skip
    ens("categorization", "approve", "--by", "Marta Gil")


def ui_script(session: Session) -> None:
    def post(step: str, data: dict) -> None:
        response = session.post(f"/asistente/{step}", data)
        assert response.status_code == 303, response.text

    post("sistema", {"name": "Sistema de prueba", "description": "Para comparar"})
    post("activos", {
        "accion": "anadir", "asset_type": "laptop", "asset_id": "lap-nuevo", "asset_os": "macOS",
        "asset_owner": "Ana Ruiz", "asset_encrypted": "true", "asset_usage": "Trabajo",
    })  # fmt: skip
    post("servicios", {
        "included_services": ["svc-facturacion"], "service_id": "svc-soporte",
        "service_name": "Soporte", "service_assets": ["lap-nuevo", "srv-app"],
    })  # fmt: skip
    post("dependencias", {
        "dependency_id": "dep-nuevo", "asset": "lap-nuevo", "depends_on": "srv-app",
        "purpose": "Acceso por SSH",
    })  # fmt: skip
    post("aprobacion-alcance", {"accion": "aprobar", "approved_by": "Ana Ruiz"})
    post("informacion", {"info_id": "datos", "description": "Datos de clientes"})
    form = {}
    for kind, element in ELEMENTS:
        for dimension in Dimension:
            base = f"rating~{kind}~{element}~{dimension.value}"
            form[f"{base}~level"] = LEVELS[element]
            form[f"{base}~justification"] = JUSTIFICATION
        form[f"valued_by~{kind}~{element}"] = "Ana Ruiz"
    post("valoraciones", form)
    post("aprobacion-categorizacion", {"accion": "aprobar", "approved_by": "Marta Gil"})


def comparable(store: LocalStore) -> dict:
    """YAML ya interpretado; el resto, byte a byte."""
    return {
        key: yaml.safe_load(data) if key.endswith(".yaml") else data
        for key, data in snapshot_of(store).items()
    }


def fingerprints(store: LocalStore) -> tuple:
    scope = scope_status(store)
    categorization = categorization_status(store, NOW)
    assert scope.state is ScopeState.APPROVED
    assert categorization.state is CategorizationState.CURRENT
    return (
        scope.fingerprint,
        scope.approval,
        categorization.approval,
        categorization.computation.category,
    )


def run_cli(tmp_path: Path, store: LocalStore, monkeypatch, script=None) -> None:
    settings = tmp_path / "settings.yaml"
    settings.write_text(
        yaml.safe_dump(
            {"workspace": {"backend": "local", "local": {"path": str(tmp_path / "cli-ws")}}}
        )
    )
    monkeypatch.setattr(cli, "clock", lambda: NOW)

    def ens(*args: str) -> None:
        result = runner.invoke(cli.app, ["--settings", str(settings), *args])
        assert result.exit_code == 0, (args, result.output)

    if script is None:
        cli_script(ens)
    else:
        ens(*script)


def assert_ui_and_cli_produce_the_same_workspace(
    tmp_path, workspaces, config, key, token, monkeypatch
):
    cli_store, ui_store = workspaces
    run_cli(tmp_path, cli_store, monkeypatch)
    ui_script(Session(ui_client(config, ui_store, key), token))

    keys = set(snapshot_of(cli_store))
    assert {"scope/approval.yaml", "scope/categorization.yaml"} <= keys
    assert "scope/categorization-approval.yaml" in keys
    assert keys == set(snapshot_of(ui_store))
    assert comparable(cli_store) == comparable(ui_store)
    assert fingerprints(cli_store) == fingerprints(ui_store)


def test_ui_and_cli_produce_the_same_workspace(
    tmp_path, workspaces, config, key, token, monkeypatch
):
    assert_ui_and_cli_produce_the_same_workspace(
        tmp_path, workspaces, config, key, token, monkeypatch
    )


def test_including_an_existing_asset_is_the_same_in_ui_and_cli(
    tmp_path, workspaces, config, key, token, monkeypatch
):
    cli_store, ui_store = workspaces
    for store in workspaces:
        system = yaml.safe_load(store.read_text("scope/system.yaml"))
        system["included_assets"].remove("bk-externo")
        store.write_text("scope/system.yaml", yaml.safe_dump(system, sort_keys=False))
    included = yaml.safe_load(ui_store.read_text("scope/system.yaml"))["included_assets"]
    run_cli(tmp_path, cli_store, monkeypatch, ("scope", "include-asset", "--id", "bk-externo"))
    session = Session(ui_client(config, ui_store, key), token)

    response = session.post("/asistente/activos", {"included_assets": [*included, "bk-externo"]})

    assert response.status_code == 303, response.text
    assert (
        "bk-externo" in yaml.safe_load(cli_store.read_text("scope/system.yaml"))["included_assets"]
    )
    assert comparable(cli_store) == comparable(ui_store)
    assert snapshot_of(cli_store) == snapshot_of(ui_store)


def test_unauthenticated_requests_write_nothing(config, workspaces, key):
    _, ui_store = workspaces
    before = snapshot_of(ui_store)
    client = ui_client(config, ui_store, key)
    headers = {"Origin": "https://ui.example.com", "Sec-Fetch-Site": "same-origin"}

    responses = [
        client.post("/asistente/sistema", data={"name": "x", "description": "y"}, headers=headers),
        client.post(
            "/asistente/aprobacion-alcance",
            data={"accion": "aprobar", "approved_by": "x"},
            headers=headers,
        ),
        client.get("/asistente/sistema"),
        client.get("/"),
    ]

    assert [r.status_code for r in responses] == [401, 401, 401, 401]
    assert snapshot_of(ui_store) == before


def test_wizard_store_is_the_whitelisted_one(raw_client, example_store):
    from ens_evidence.ui.store import WriteNotAllowed

    store = raw_client.app.state.store
    before = snapshot_of(example_store)

    with pytest.raises(WriteNotAllowed):
        store.write_text("connectors/github.yaml", "token_env: X\n")

    assert snapshot_of(example_store) == before
