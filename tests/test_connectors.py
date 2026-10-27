from importlib.metadata import EntryPoint

import pytest
from fake_connector import FakeConnector

from ens_evidence.connectors import (
    ENTRY_POINT_GROUP,
    Connector,
    ConnectorLoadError,
    ConnectorRegistry,
)


def entry_point(name="fake", attr="FakeConnector", module="fake_connector"):
    return EntryPoint(name=name, value=f"{module}:{attr}", group=ENTRY_POINT_GROUP)


def test_grupo_de_entry_points_es_el_documentado():
    assert ENTRY_POINT_GROUP == "ens_evidence.connectors"


def test_descubre_un_conector_por_entry_point_y_lo_ejecuta():
    registry = ConnectorRegistry()
    registry.load_entry_points([entry_point()])
    connector_class = registry.get("fake")
    assert connector_class is FakeConnector
    result = connector_class().collect(connector_class.config_model(asset_id="srv-7"))
    assert result.asset_id == "srv-7"


def test_registro_explicito_de_un_conector():
    registry = ConnectorRegistry()
    registry.register(FakeConnector)
    assert registry.names() == ["fake"]


def test_conector_desconocido_lanza_keyerror():
    with pytest.raises(KeyError, match="unknown connector"):
        ConnectorRegistry().get("nope")


def test_nombre_duplicado_se_rechaza():
    registry = ConnectorRegistry()
    registry.register(FakeConnector)
    with pytest.raises(ConnectorLoadError, match="duplicate"):
        registry.register(FakeConnector)


def test_objeto_que_no_es_conector_se_rechaza():
    with pytest.raises(ConnectorLoadError, match="not a Connector"):
        ConnectorRegistry().register(object)


def test_conector_sin_nombre_o_config_se_rechaza():
    class Incomplete(Connector):
        def collect(self, config):
            raise NotImplementedError

    with pytest.raises(ConnectorLoadError, match="lacks"):
        ConnectorRegistry().register(Incomplete)


def test_entry_point_que_no_carga_lanza_connector_load_error():
    with pytest.raises(ConnectorLoadError, match="cannot load"):
        ConnectorRegistry().load_entry_points([entry_point(module="no_such_module")])


def test_nombre_de_entry_point_debe_coincidir_con_el_del_conector_y_no_deja_registro():
    registry = ConnectorRegistry()
    with pytest.raises(ConnectorLoadError, match="loads connector"):
        registry.load_entry_points([entry_point(name="fake", attr="MisnamedConnector")])
    assert registry.names() == []


def test_conector_abstracto_no_se_puede_instanciar_sin_collect():
    with pytest.raises(TypeError):
        Connector()
