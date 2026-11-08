import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

import ens_evidence.cli as cli
from ens_evidence.catalog.model import Dimension
from ens_evidence.categorization import (
    CATEGORIZATION_KEY,
    CategorizationFile,
    CategorizationState,
    categorization_status,
    init_categorization,
)
from ens_evidence.categorization.edit import (
    CategorizationEditRejected,
    add_information,
    set_rating,
    set_valuation,
)
from ens_evidence.cli import app
from ens_evidence.scope import load_inventory
from ens_evidence.scope.inventory import fingerprint
from ens_evidence.workspace import LocalStore

from .helpers import EXAMPLE_SCOPE, NOW

runner = CliRunner()
TOKEN = "ghp_" + "A1b2C3d4E5" * 4
JUSTIFICATION = "Criterio 1.º del anexo I, 3"


@pytest.fixture
def template(store) -> LocalStore:
    assert init_categorization(store)
    return store


def load(store: LocalStore) -> CategorizationFile:
    return CategorizationFile.model_validate(yaml.safe_load(store.read_text(CATEGORIZATION_KEY)))


def rate_all(store: LocalStore, kind: str, element_id: str, level: str) -> None:
    for dimension in Dimension:
        set_rating(store, kind, element_id, dimension, level, JUSTIFICATION, NOW)


def test_flujo_completo_deja_el_fichero_aprobable(template):
    add_information(template, "datos-clientes", "Datos de los clientes", NOW)
    for kind, element_id in (("information", "datos-clientes"), ("service", "svc-facturacion")):
        rate_all(template, kind, element_id, "MEDIO")
        set_valuation(template, kind, element_id, "Ana Ruiz", NOW)

    status = categorization_status(template, NOW)

    assert status.state is CategorizationState.NOT_APPROVED, (status.errors, status.pending)
    stored = load(template)
    assert stored.information[0].valuation.role.value == "information_owner"
    assert stored.services[0].valuation.role.value == "service_owner"
    assert stored.services[0].valuation.valued_at == NOW


def test_conserva_la_cabecera_de_la_plantilla(template):
    header = template.read_text(CATEGORIZATION_KEY).split("\ninformation:")[0]

    add_information(template, "datos-clientes", "Datos de los clientes", NOW)

    assert template.read_text(CATEGORIZATION_KEY).startswith(header)
    assert "BAJO: perjuicio limitado" in template.read_text(CATEGORIZATION_KEY)


def test_la_misma_edicion_da_el_mismo_fichero(store, tmp_path):
    other = LocalStore(tmp_path / "other")
    for name in ("assets", "services", "dependencies", "system"):
        other.write_text(f"scope/{name}.yaml", store.read_text(f"scope/{name}.yaml"))
    for target in (store, other):
        target.write_text(CATEGORIZATION_KEY, "information: []\nservices: []\n")
        add_information(target, "a", "A", NOW)
        set_rating(target, "information", "a", Dimension.C, "ALTO", JUSTIFICATION, NOW)

    assert store.read_text(CATEGORIZATION_KEY) == other.read_text(CATEGORIZATION_KEY)
    first, second = (load_inventory(s).inventory for s in (store, other))
    assert fingerprint(first) == fingerprint(second)


def test_nivel_invalido_se_rechaza_sin_escribir(template):
    before = template.read_text(CATEGORIZATION_KEY)

    with pytest.raises(CategorizationEditRejected) as raised:
        set_rating(
            template, "service", "svc-facturacion", Dimension.C, "ENORME", JUSTIFICATION, NOW
        )

    assert "svc-facturacion" in raised.value.errors[0]
    assert template.read_text(CATEGORIZATION_KEY) == before


def test_justificacion_vacia_se_rechaza(template):
    with pytest.raises(CategorizationEditRejected, match="justificación"):
        set_rating(template, "service", "svc-facturacion", Dimension.C, "BAJO", "  ", NOW)


def test_not_affected_se_acepta_con_justificacion(template):
    set_rating(
        template, "service", "svc-facturacion", Dimension.T, "not_affected", "No aplica", NOW
    )

    assert load(template).services[0].ratings[Dimension.T].level == "not_affected"


def test_elemento_desconocido_y_id_repetido(template):
    with pytest.raises(CategorizationEditRejected, match="no hay service con id svc-nada"):
        set_rating(template, "service", "svc-nada", Dimension.C, "BAJO", JUSTIFICATION, NOW)
    add_information(template, "datos", "Datos", NOW)
    with pytest.raises(CategorizationEditRejected, match="repetido"):
        add_information(template, "datos", "Otra vez", NOW)


def test_fichero_inexistente_indica_init(store):
    with pytest.raises(CategorizationEditRejected, match="categorization init"):
        add_information(store, "datos", "Datos", NOW)


def test_secreto_se_rechaza_sin_eco_ni_escritura(template):
    before = template.read_text(CATEGORIZATION_KEY)

    with pytest.raises(CategorizationEditRejected, match="secreto") as raised:
        set_rating(template, "service", "svc-facturacion", Dimension.C, "BAJO", TOKEN, NOW)

    assert TOKEN not in str(raised.value)
    assert template.read_text(CATEGORIZATION_KEY) == before


def test_editar_deja_la_aprobacion_obsoleta(template):
    add_information(template, "datos", "Datos", NOW)
    for kind, element_id in (("information", "datos"), ("service", "svc-facturacion")):
        rate_all(template, kind, element_id, "BAJO")
        set_valuation(template, kind, element_id, "Ana Ruiz", NOW)
    from ens_evidence.categorization import approve_categorization

    approve_categorization(template, "Marta Gil", NOW)
    assert categorization_status(template, NOW).state is CategorizationState.CURRENT

    set_rating(template, "information", "datos", Dimension.C, "ALTO", JUSTIFICATION, NOW)

    assert categorization_status(template, NOW).state is CategorizationState.STALE


def test_cli_produce_un_yaml_que_aceptan_los_validadores(tmp_path, monkeypatch):
    shutil.copytree(EXAMPLE_SCOPE, tmp_path / "ws" / "scope")
    settings = tmp_path / "settings.yaml"
    settings.write_text(
        yaml.safe_dump({"workspace": {"backend": "local", "local": {"path": str(tmp_path / "ws")}}})
    )
    monkeypatch.setattr(cli, "clock", lambda: datetime(2026, 10, 28, 9, 0, tzinfo=UTC))

    def ens(*args: str):
        result = runner.invoke(app, ["--settings", str(Path(settings)), *args])
        assert result.exit_code == 0, result.output
        return result

    ens("scope", "approve", "--by", "Ana Ruiz")
    ens("categorization", "init")
    ens("categorization", "add-information", "--id", "datos", "--description", "Datos")
    for dimension in Dimension:
        for extra in ((), ("--service",)):
            element = "svc-facturacion" if extra else "datos"
            ens("categorization", "rate", "--id", element, "--dimension", dimension.value,
                "--level", "MEDIO", "--justification", JUSTIFICATION, *extra)  # fmt: skip
    ens("categorization", "sign-valuation", "--id", "datos", "--by", "Ana Ruiz")
    ens("categorization", "sign-valuation", "--id", "svc-facturacion", "--by", "Ana Ruiz",
        "--service")  # fmt: skip
    ens("categorization", "approve", "--by", "Marta Gil")

    assert "CURRENT" in ens("categorization", "status").output


def test_cli_rechaza_con_mensaje_claro(tmp_path):
    shutil.copytree(EXAMPLE_SCOPE, tmp_path / "ws" / "scope")
    settings = tmp_path / "settings.yaml"
    settings.write_text(
        yaml.safe_dump({"workspace": {"backend": "local", "local": {"path": str(tmp_path / "ws")}}})
    )

    result = runner.invoke(
        app,
        ["--settings", str(settings), "categorization", "add-information",
         "--id", "d", "--description", "D"],
    )  # fmt: skip

    assert result.exit_code == 1
    assert "No se añade la información" in result.output
    assert "categorization init" in result.output
