import ast
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ens_evidence.checks import engine, registry
from ens_evidence.checks.catalog import CHECKS, KNOWN_CHECKS
from ens_evidence.checks.registry import CHECK_FUNCTIONS, CONFIG_MODELS, ENGINE_CONNECTORS
from ens_evidence.endpoint.checks import HOST_CHECKS
from ens_evidence.matrix import technical
from ens_evidence.models.contracts import CollectionResult
from ens_evidence.models.enums import CollectionStatus

AT = datetime(2026, 10, 30, tzinfo=UTC)


def test_produced_check_ids_are_exactly_the_known_checks():
    produced = {check_id for spec in ENGINE_CONNECTORS.values() for check_id in spec.check_ids}

    assert produced == set(KNOWN_CHECKS)
    assert produced == set(technical.KNOWN_CHECKS)


def test_each_check_belongs_to_exactly_one_connector():
    ids = [check_id for spec in ENGINE_CONNECTORS.values() for check_id in spec.check_ids]

    assert len(ids) == len(set(ids))
    for name, spec in ENGINE_CONNECTORS.items():
        assert all(KNOWN_CHECKS[check_id] == name for check_id in spec.check_ids)
        assert all(CHECKS[check_id].connector == name for check_id in spec.check_ids)


def test_connectors_are_the_catalog_connectors_and_each_has_a_config_model():
    assert set(ENGINE_CONNECTORS) == set(KNOWN_CHECKS.values())
    assert set(CONFIG_MODELS) == set(ENGINE_CONNECTORS)
    for name, spec in ENGINE_CONNECTORS.items():
        assert spec.name == name
        assert spec.config_model is CONFIG_MODELS[name]


def test_collector_connectors_have_a_function_per_check_and_endpoint_has_none():
    for name, spec in ENGINE_CONNECTORS.items():
        if name == "endpoint":
            assert dict(spec.checks) == {}
        else:
            assert tuple(spec.checks) == spec.check_ids


def test_endpoint_checks_are_the_host_checks():
    assert set(ENGINE_CONNECTORS["endpoint"].check_ids) == set(HOST_CHECKS)


@pytest.mark.parametrize("check_id", sorted(CHECK_FUNCTIONS))
def test_each_function_produces_results_for_its_own_id(check_id):
    failed = CollectionResult(
        source="x",
        collector="x",
        collected_at=AT,
        asset_id="asset",
        data={"error": "fallo", "sections": {}, "organization": "org"},
        collection_status=CollectionStatus.ERROR,
    )

    results = CHECK_FUNCTIONS[check_id](failed, "EV-1", AT)

    assert results
    assert {r.check_id for r in results} == {check_id}


def test_registry_has_no_second_catalog_of_ids():
    source = Path(registry.__file__).read_text()
    literals = {
        node.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert not literals & set(KNOWN_CHECKS)


@pytest.mark.parametrize("module", [registry, engine])
def test_registry_and_engine_never_import_connector_classes_or_the_entry_point_registry(module):
    imported = set()
    for node in ast.walk(ast.parse(Path(module.__file__).read_text())):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            imported.update(f"{node.module}.{a.name}" for a in node.names)
        elif isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)

    forbidden = {
        name
        for name in imported
        if name.endswith(".connector")
        or name.endswith("connectors.registry")
        or name.endswith(".client")
        or name == "importlib.metadata"
    }
    assert not forbidden
