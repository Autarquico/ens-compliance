"""El registro del kit: lo que cuenta de cada herramienta existe de verdad."""

import re
from collections import Counter
from pathlib import Path

import pytest
import yaml

from ens_evidence.catalog.load import load_catalog
from ens_evidence.connectors.registry import ConnectorRegistry
from ens_evidence.kit.registry import KIT_TOOLS, KitTool, tool_for
from ens_evidence.ui.config import load_ui_config
from ens_evidence.ui.views.help import RUNBOOKS, RUNBOOKS_DIR

ROOT = Path(__file__).resolve().parents[2]
BY_ID = {tool.id: tool for tool in KIT_TOOLS}
CATALOG = {measure.id: measure for measure in load_catalog().measures}


def _clause_ids(measure_id: str) -> set[str]:
    measure = CATALOG[measure_id]
    clauses = [*measure.base, *(c for r in measure.reinforcements for c in r.clauses)]
    return {clause.id for clause in clauses}


@pytest.fixture(params=KIT_TOOLS, ids=lambda tool: tool.id)
def tool(request) -> KitTool:
    return request.param


def test_las_medidas_existen_en_el_catalogo(tool):
    assert tool.measures
    assert set(tool.measures) <= set(CATALOG)


def test_las_clausulas_existen_y_son_de_una_medida_de_la_herramienta(tool):
    known = set().union(*(_clause_ids(m) for m in tool.measures))
    listed = [*tool.covers_clauses, *tool.partial_clauses, *tool.not_covered_clauses]
    assert set(listed) <= known
    assert len(listed) == len(set(listed)), "una cláusula no puede estar en dos listas"


def test_el_conector_esta_registrado(tool):
    registry = ConnectorRegistry()
    registry.load_entry_points()
    assert tool.connector in registry.names()


def test_el_runbook_existe_en_la_ayuda(tool):
    assert tool.help_slug in RUNBOOKS
    assert (RUNBOOKS_DIR / f"{tool.help_slug}.md").is_file()


def test_ninguna_medida_tiene_dos_herramientas():
    counted = Counter(m for t in KIT_TOOLS for m in t.measures)
    assert [m for m, n in counted.items() if n > 1] == []


def test_tool_for_devuelve_la_herramienta_de_la_medida_o_none():
    assert tool_for("mp.info.6") is BY_ID["copias"]
    assert tool_for("op.exp.10") is BY_ID["claves"]
    assert tool_for("op.acc.6") is BY_ID["contrasenas"]
    assert tool_for("op.acc.1") is None


def test_contrasenas_no_declara_cobertura_de_ninguna_clausula():
    tool = BY_ID["contrasenas"]
    assert tool.covers_clauses == () and tool.partial_clauses == ()
    assert set(tool.not_covered_clauses) == {"op.acc.6.4", "op.acc.6.r1.2", "op.acc.6.r2.1"}
    assert "no la suspende" in tool.covers
    assert not re.search(r"certific|garantiza el cumplimiento|cumple el ENS", tool.covers, re.I)


def test_los_ids_del_registro_son_unicos():
    assert len(BY_ID) == len(KIT_TOOLS)


def test_la_imagen_de_la_ram_es_la_de_compose(tool):
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
    images = {s["image"] for s in compose["services"].values() if "image" in s}
    assert tool.ram is not None
    assert tool.ram.image in images, "la imagen cambió: la RAM medida ya no vale, hay que medirla"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", tool.ram.measured_on)


def test_cada_herramienta_tiene_clave_en_los_hosts_de_la_config(tool):
    env = {
        "ENS_UI_ORIGIN": "https://ens.example.org",
        "ENS_UI_ACCESS_AUD": "a",
        "ENS_UI_ACCESS_TEAM_DOMAIN": "acme.cloudflareaccess.com",
        "ENS_UI_ACCESS_JWKS_FILE": "/x",
    }
    assert load_ui_config(env).kit_hosts[tool.id] is None
    with_host = load_ui_config({**env, f"ENS_{tool.id.upper()}_HOST": "Kit.Example.org"})
    assert with_host.kit_hosts[tool.id] == "kit.example.org"
