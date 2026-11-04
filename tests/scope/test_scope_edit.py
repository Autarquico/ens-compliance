from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from ens_evidence.cli import app
from ens_evidence.scope import ScopeState, load_inventory, scope_status
from ens_evidence.scope.edit import ScopeEditRejected, parse_field
from ens_evidence.workspace import LocalStore

runner = CliRunner()
SERVER = [
    "--set", "hostname=caja.ejemplo.org",
    "--set", "function=Caja ens-compliance",
    "--set", "os=Debian 13",
    "--set", "environment=producción",
    "--set", "location=Oficina",
]  # fmt: skip


def ens(settings: Path, *args: str):
    return runner.invoke(app, ["--settings", str(settings), *args])


@pytest.fixture
def fresh(tmp_path) -> tuple[Path, LocalStore]:
    settings = tmp_path / "settings.yaml"
    settings.write_text(
        yaml.safe_dump({"workspace": {"backend": "local", "local": {"path": str(tmp_path / "ws")}}})
    )
    assert ens(settings, "init").exit_code == 0
    return settings, LocalStore(tmp_path / "ws")


def enrol(settings: Path, *extra: str):
    return ens(
        settings, "scope", "add-asset", "--id", "srv-caja", "--type", "server", *SERVER, *extra
    )


def test_instalacion_limpia_inscribe_la_caja_y_se_puede_aprobar(fresh):
    settings, store = fresh
    assert enrol(settings, "--include").exit_code == 0
    assert (
        ens(
            settings, "scope", "set-system", "--name", "Autarqui", "--description", "SaaS"
        ).exit_code
        == 0
    )
    assert load_inventory(store).errors == []
    assert scope_status(store).state is ScopeState.NOT_APPROVED
    assert ens(settings, "scope", "approve", "--by", "Ana").exit_code == 0
    assert scope_status(store).state is ScopeState.APPROVED


def test_no_aprueba_por_su_cuenta(fresh):
    settings, store = fresh
    enrol(settings, "--include")
    assert scope_status(store).state is not ScopeState.APPROVED
    assert not store.exists("scope/approval.yaml")


def test_sin_include_el_activo_queda_sin_decidir(fresh):
    settings, store = fresh
    assert enrol(settings).exit_code == 0
    ens(settings, "scope", "set-system", "--name", "A", "--description", "B")
    errors = load_inventory(store).errors
    assert any("no está incluido ni excluido" in e for e in errors)


def test_rechaza_id_repetido_y_campos_que_faltan(fresh):
    settings, store = fresh
    enrol(settings, "--include")
    again = enrol(settings, "--include")
    assert again.exit_code == 1 and "ya existe" in again.stderr
    missing = ens(settings, "scope", "add-asset", "--id", "otro", "--type", "server")
    assert missing.exit_code == 1 and "necesita estos campos" in missing.stderr
    assert "otro" not in store.read_text("scope/assets.yaml")


def test_rechaza_un_secreto(fresh):
    settings, _ = fresh
    result = ens(
        settings, "scope", "add-asset", "--id", "x", "--type", "server", *SERVER,
        "--set", "owner=ghp_" + "A1b2C3d4E5" * 4,
    )  # fmt: skip
    assert result.exit_code == 1 and "secreto" in result.stderr


def test_add_asset_nombra_el_campo_con_secreto_y_nunca_el_valor(fresh):
    settings, store = fresh
    secret = "ghp_" + "A1b2C3d4E5" * 4
    result = ens(
        settings, "scope", "add-asset", "--id", "x", "--type", "server", *SERVER,
        "--set", f"owner={secret}",
    )  # fmt: skip
    assert result.exit_code == 1 and "owner" in result.stderr
    assert secret not in result.stdout + result.stderr
    assert "id: x" not in store.read_text("scope/assets.yaml")


@pytest.mark.parametrize("field", ["name", "description"])
def test_set_system_rechaza_un_secreto_antes_de_escribir(fresh, field):
    settings, store = fresh
    secret = "ghp_" + "A1b2C3d4E5" * 4
    before = store.read_text("scope/system.yaml")
    values = {"name": "Autarqui", "description": "SaaS", field: secret}
    result = ens(
        settings, "scope", "set-system", "--name", values["name"],
        "--description", values["description"],
    )  # fmt: skip
    assert result.exit_code == 1 and field in result.stderr
    assert secret not in result.stdout + result.stderr
    assert store.read_text("scope/system.yaml") == before


def test_cambiar_el_alcance_revoca_la_aprobacion(fresh):
    settings, store = fresh
    enrol(settings, "--include")
    ens(settings, "scope", "set-system", "--name", "A", "--description", "B")
    ens(settings, "scope", "approve", "--by", "Ana")
    result = ens(settings, "scope", "set-system", "--name", "C", "--description", "B")
    assert "revoca la aprobación" in result.stdout
    assert scope_status(store).state is not ScopeState.APPROVED


def test_conserva_los_activos_anteriores_y_escribe_yaml_de_bloque(fresh):
    settings, store = fresh
    enrol(settings, "--include")
    ens(
        settings, "scope", "add-asset", "--id", "portatil", "--type", "laptop",
        "--set", "os=macOS", "--set", "owner=Ana", "--set", "encrypted=true",
        "--set", "usage=oficina",
    )  # fmt: skip
    data = yaml.safe_load(store.read_text("scope/assets.yaml"))
    assert [a["id"] for a in data["assets"]] == ["srv-caja", "portatil"]
    assert data["assets"][1]["encrypted"] is True


@pytest.mark.parametrize(
    ("name", "text", "expected"),
    [
        ("encrypted", "true", True),
        ("encrypted", " Sí ", True),
        ("mfa", "no", False),
        ("repos", "a, b,,c", ["a", "b", "c"]),
        ("zones", "una", ["una"]),
        ("os", "Debian, 13 (bool list)", "Debian, 13 (bool list)"),
        ("campo_desconocido", "true", "true"),
    ],
)
def test_parse_field_convierte_segun_el_tipo_del_campo(name, text, expected):
    assert parse_field(name, text) == expected


def test_parse_field_rechaza_un_booleano_que_no_lo_es():
    with pytest.raises(ScopeEditRejected, match="indica true o false"):
        parse_field("encrypted", "quizá")
