from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

import ens_evidence.cli as cli
from ens_evidence.cli import app
from ens_evidence.models.enums import Role
from ens_evidence.scope import ScopeState, approve_scope, load_inventory, scope_status
from ens_evidence.scope.edit import (
    ScopeEditRejected,
    add_dependency,
    add_service,
    include_service,
)
from ens_evidence.scope.inventory import fingerprint
from ens_evidence.workspace import LocalStore

from .scope_helpers import APPROVED_AT, TOKEN

NOW = datetime(2026, 10, 28, 9, 0, tzinfo=UTC)
runner = CliRunner()


@pytest.fixture
def approved(store) -> LocalStore:
    approve_scope(store, "Ana Ruiz", Role.SYSTEM_OWNER, APPROVED_AT)
    return store


def test_add_service_incluido_queda_en_el_inventario_y_revoca(approved):
    revoked = add_service(approved, "svc-soporte", "Soporte", ["srv-app"], True, NOW)

    inventory = load_inventory(approved).inventory
    assert revoked is True
    assert inventory is not None
    assert "svc-soporte" in {s.id for s in inventory.services.services}
    assert "svc-soporte" in inventory.system.included_services
    assert scope_status(approved).state is not ScopeState.APPROVED


def test_add_service_sin_include_queda_sin_decidir(approved):
    add_service(approved, "svc-soporte", "Soporte", ["srv-app"], False, NOW)

    assert any("no está incluido ni excluido" in e for e in load_inventory(approved).errors)


def test_add_service_rechaza_id_repetido_y_servicio_sin_activos(approved):
    with pytest.raises(ScopeEditRejected, match="ya existe"):
        add_service(approved, "svc-facturacion", "Otro", ["srv-app"], False, NOW)
    with pytest.raises(ScopeEditRejected) as sin_activos:
        add_service(approved, "svc-x", "X", [], False, NOW)
    assert "services.yaml" in sin_activos.value.errors[0]
    assert scope_status(approved).state is ScopeState.APPROVED


def test_add_service_rechaza_secretos_sin_escribir(approved):
    before = approved.read_text("scope/services.yaml")

    with pytest.raises(ScopeEditRejected, match="secreto") as raised:
        add_service(approved, "svc-x", TOKEN, ["srv-app"], False, NOW)

    assert TOKEN not in str(raised.value)
    assert approved.read_text("scope/services.yaml") == before
    assert scope_status(approved).state is ScopeState.APPROVED


def test_include_service_incluye_y_revoca(approved):
    add_service(approved, "svc-soporte", "Soporte", ["srv-app"], False, NOW)

    revoked = include_service(approved, "svc-soporte", NOW)

    assert revoked is True
    assert "scope include-service" in approved.read_text("scope/approval.yaml")
    assert load_inventory(approved).errors == []
    inventory = load_inventory(approved).inventory
    assert inventory is not None
    assert "svc-soporte" in inventory.system.included_services


def test_include_service_desconocido_se_rechaza(approved):
    with pytest.raises(ScopeEditRejected, match="svc-nada no existe"):
        include_service(approved, "svc-nada", NOW)


def test_include_service_ya_incluido_no_cambia_ni_revoca(approved):
    before = approved.read_text("scope/system.yaml")

    assert include_service(approved, "svc-facturacion", NOW) is False

    assert approved.read_text("scope/system.yaml") == before
    assert scope_status(approved).state is ScopeState.APPROVED


def test_add_dependency_valida_y_revoca(approved):
    revoked = add_dependency(approved, "dep-nueva", "srv-app", "prov-nube", "Alojamiento", NOW)

    inventory = load_inventory(approved).inventory
    assert revoked is True
    assert inventory is not None
    assert "dep-nueva" in {d.id for d in inventory.dependencies.dependencies}
    assert scope_status(approved).state is not ScopeState.APPROVED


def test_add_dependency_rechaza_repetida_vacia_y_secreto(approved):
    with pytest.raises(ScopeEditRejected, match="ya existe"):
        add_dependency(approved, "dep-app-db", "srv-app", "prov-nube", None, NOW)
    with pytest.raises(ScopeEditRejected):
        add_dependency(approved, "dep-x", "", "prov-nube", None, NOW)
    with pytest.raises(ScopeEditRejected, match="secreto"):
        add_dependency(approved, "dep-y", "srv-app", "prov-nube", TOKEN, NOW)
    assert scope_status(approved).state is ScopeState.APPROVED


def test_la_huella_es_estable_para_el_mismo_contenido(store, tmp_path):
    other = LocalStore(tmp_path / "other")
    for name in ("assets", "services", "dependencies", "system"):
        other.write_text(f"scope/{name}.yaml", store.read_text(f"scope/{name}.yaml"))

    for target in (store, other):
        add_service(target, "svc-soporte", "Soporte", ["srv-app"], True, NOW)
        add_dependency(target, "dep-nueva", "srv-app", "prov-nube", None, NOW)

    first, second = (load_inventory(s).inventory for s in (store, other))
    assert first is not None and second is not None
    assert fingerprint(first) == fingerprint(second)
    assert store.read_text("scope/services.yaml") == other.read_text("scope/services.yaml")


def test_cli_escribe_con_clock_y_el_resultado_pasa_los_validadores(tmp_path, store, monkeypatch):
    settings = tmp_path / "settings.yaml"
    settings.write_text(
        yaml.safe_dump({"workspace": {"backend": "local", "local": {"path": str(tmp_path / "ws")}}})
    )
    monkeypatch.setattr(cli, "clock", lambda: APPROVED_AT)

    def ens(*args: str):
        return runner.invoke(app, ["--settings", str(Path(settings)), *args])

    assert ens("scope", "approve", "--by", "Ana Ruiz").exit_code == 0
    added = ens(
        "scope", "add-service", "--id", "svc-soporte", "--name", "Soporte",
        "--asset", "srv-app", "--asset", "app-facturas", "--include",
    )  # fmt: skip
    dependency = ens(
        "scope", "add-dependency", "--id", "dep-nueva", "--asset", "srv-app",
        "--depends-on", "prov-nube", "--purpose", "Alojamiento",
    )  # fmt: skip

    assert added.exit_code == 0, added.output
    assert "Se revoca la aprobación" in added.output
    assert dependency.exit_code == 0, dependency.output
    assert load_inventory(store).errors == []
    assert "revoked_at: '2026-10-27T09:00:00Z'" in store.read_text("scope/approval.yaml")


def test_cli_rechaza_con_mensaje_claro(tmp_path, store):
    settings = tmp_path / "settings.yaml"
    settings.write_text(
        yaml.safe_dump({"workspace": {"backend": "local", "local": {"path": str(tmp_path / "ws")}}})
    )

    result = runner.invoke(
        app, ["--settings", str(settings), "scope", "include-service", "--id", "svc-nada"]
    )

    assert result.exit_code == 1
    assert "No se incluye el servicio" in result.output
    assert "svc-nada no existe" in result.output


def test_cli_include_asset_incluye_un_activo_existente_y_rechaza_el_desconocido(tmp_path, store):
    settings = tmp_path / "settings.yaml"
    settings.write_text(
        yaml.safe_dump({"workspace": {"backend": "local", "local": {"path": str(tmp_path / "ws")}}})
    )

    def ens(*args):
        return runner.invoke(app, ["--settings", str(settings), *args])

    added = ens(
        "scope", "add-asset", "--id", "lap-nuevo", "--type", "laptop", "--set", "os=macOS",
        "--set", "owner=Ana Ruiz", "--set", "encrypted=true", "--set", "usage=Trabajo",
    )  # fmt: skip
    included = ens("scope", "include-asset", "--id", "lap-nuevo")
    unknown = ens("scope", "include-asset", "--id", "lap-nada")

    assert added.exit_code == 0, added.output
    assert included.exit_code == 0, included.output
    assert "lap-nuevo" in store.read_text("scope/system.yaml")
    assert unknown.exit_code == 1
    assert "No se incluye el activo" in unknown.output
    assert "lap-nada no existe" in unknown.output
