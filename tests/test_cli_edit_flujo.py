import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from ens_evidence.catalog.controls import init_measures
from ens_evidence.cli import app
from ens_evidence.models.enums import Role
from ens_evidence.orgdocs.answers import read_answers
from ens_evidence.scope import approve_scope
from ens_evidence.workspace import LocalStore

NOW = datetime(2026, 10, 28, 9, 0, tzinfo=UTC)
LATER = datetime(2026, 10, 29, 9, 0, tzinfo=UTC)
EXAMPLE_SCOPE = Path(__file__).resolve().parents[1] / "examples" / "workspace" / "scope"
REASON = "Decisión de la organización tras revisar el riesgo."
RISK_ARGS = [
    "risk", "add", "--id", "R-001", "--asset", "app-facturas", "--service", "svc-facturacion",
    "--threat", "Acceso no autorizado", "--dimension", "C", "--valuation", "C=ALTO",
    "--likelihood", "media", "--impact", "alto", "--inherent", "alto", "--safeguard", "op.acc.1",
    "--residual", "bajo", "--treatment", "mitigate", "--owner", "Ana Ruiz",
]  # fmt: skip


@pytest.fixture
def store(tmp_path) -> LocalStore:
    shutil.copytree(EXAMPLE_SCOPE, tmp_path / "ws" / "scope")
    workspace = LocalStore(tmp_path / "ws")
    approve_scope(workspace, "Ana Ruiz", Role.SYSTEM_OWNER, NOW)
    return workspace


@pytest.fixture
def ens(tmp_path, store, monkeypatch):
    monkeypatch.setattr("ens_evidence.cli.clock", lambda: LATER)
    settings = tmp_path / "settings.yaml"
    settings.write_text(
        yaml.safe_dump({"workspace": {"backend": "local", "local": {"path": str(tmp_path / "ws")}}})
    )
    runner = CliRunner()
    return lambda *args: runner.invoke(app, ["--settings", str(settings), *args])


def adopt(ens):
    return ens("risk", "adopt-methodology", "--name", "M", "--reference", "R", "--adaptation", "A")


def test_el_flujo_de_riesgos_por_cli_deja_un_analisis_valido(ens, store):
    assert adopt(ens).exit_code == 0
    assert ens(*RISK_ARGS).exit_code == 0
    added = ens(
        "risk", "add-action", "--id", "A-001", "--risk", "R-001", "--description", "Activar MFA",
        "--owner", "Ana Ruiz", "--due", "2026-12-31",
    )  # fmt: skip
    assert added.exit_code == 0, added.output

    register = yaml.safe_load(store.read_text("risk/register.yaml"))
    assert register["risks"][0]["valuation"] == {"C": "ALTO"}
    plan = yaml.safe_load(store.read_text("risk/treatment-plan.yaml"))
    assert plan["actions"][0]["status"] == "pending"


def test_risk_add_con_valoracion_mal_formada_sale_con_1_y_no_escribe(ens, store):
    adopt(ens)
    args = [a if a != "C=ALTO" else "ALTO" for a in RISK_ARGS]

    result = ens(*args)

    assert result.exit_code == 1
    assert "dimensión=nivel" in result.output
    assert yaml.safe_load(store.read_text("risk/register.yaml"))["risks"] == []


def test_risk_remove_y_remove_action_quitan_lo_indicado(ens, store):
    adopt(ens)
    ens(*RISK_ARGS)
    ens(
        "risk", "add-action", "--id", "A-001", "--risk", "R-001", "--description", "d",
        "--owner", "o", "--due", "2026-12-31",
    )  # fmt: skip

    assert ens("risk", "remove", "--id", "R-001").exit_code == 1
    assert ens("risk", "remove-action", "--id", "A-001").exit_code == 0
    assert ens("risk", "remove", "--id", "R-001").exit_code == 0


def test_docs_answer_guarda_texto_y_lista(ens, store):
    text = ens("docs", "answer", "organizacion", "--value", "Acme S.L.")
    items = ens(
        "docs", "answer", "uso_equipos", "--value", "Solo equipos de la empresa",
        "--value", "Bloqueo a los 5 minutos",
    )  # fmt: skip
    one = ens("docs", "answer", "uso_equipos", "--value", "Solo equipos de la empresa")

    assert (text.exit_code, items.exit_code, one.exit_code) == (0, 0, 0), items.output
    assert read_answers(store)["uso_equipos"] == ["Solo equipos de la empresa"]
    assert read_answers(store)["organizacion"] == "Acme S.L."


def test_docs_answer_con_objetos_usa_json_y_rechaza_lo_que_no_encaja(ens, store):
    person = '[{"nombre": "Ana Ruiz", "cargo": "Gerente", "contacto": "ana@acme.example"}]'
    assert ens("docs", "answer", "personas", "--json", person).exit_code == 0
    assert read_answers(store)["personas"][0]["nombre"] == "Ana Ruiz"

    unknown = ens("docs", "answer", "no_existe", "--value", "x")
    assert unknown.exit_code == 1
    assert "pregunta desconocida" in unknown.output
    assert ens("docs", "answer", "organizacion").exit_code == 2


def test_applicability_decide_registra_la_decision_o_explica_el_rechazo(ens, store):
    from ens_evidence.categorization import approve_categorization

    _categorize(store)
    approve_categorization(store, "Luis Pardo", NOW)
    init_measures(store)

    ok = ens(
        "applicability", "decide", "op.ext.3", "--applicability", "NOT_APPLICABLE",
        "--justification", REASON,
    )  # fmt: skip
    blank = ens(
        "applicability", "decide", "op.ext.3", "--applicability", "NOT_APPLICABLE",
        "--justification", " ",
    )  # fmt: skip

    assert ok.exit_code == 0, ok.output
    assert blank.exit_code == 1
    measures = yaml.safe_load(store.read_text("controls/measures.yaml"))["measures"]
    decided = next(m for m in measures if m["id"] == "op.ext.3")
    assert decided["applicability"] == "NOT_APPLICABLE"
    assert decided["justification"] == REASON


def _categorize(store: LocalStore) -> None:
    ratings = {
        d: {"level": "MEDIO", "justification": "Criterio 1.º del anexo I, 3"} for d in "CITAD"
    }
    valuation = {"valued_by": "Ana Ruiz", "valued_at": NOW.isoformat()}
    document = {
        "information": [
            {
                "id": "datos",
                "description": "Datos",
                "ratings": ratings,
                "valuation": valuation | {"role": "information_owner"},
            }
        ],
        "services": [
            {
                "id": "svc-facturacion",
                "ratings": ratings,
                "valuation": valuation | {"role": "service_owner"},
            }
        ],
    }
    store.write_text("scope/categorization.yaml", yaml.safe_dump(document))
