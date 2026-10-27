"""Registro explícito y descubrimiento por entry points."""

from collections.abc import Iterable
from importlib.metadata import EntryPoint, entry_points

from ens_evidence.connectors.base import Connector

ENTRY_POINT_GROUP = "ens_evidence.connectors"


class ConnectorLoadError(Exception):
    pass


class ConnectorRegistry:
    def __init__(self) -> None:
        self._connectors: dict[str, type[Connector]] = {}

    def register(self, connector: type[Connector]) -> None:
        if not (isinstance(connector, type) and issubclass(connector, Connector)):
            raise ConnectorLoadError(f"{connector!r} is not a Connector subclass")
        for attribute in ("name", "config_model"):
            if not hasattr(connector, attribute):
                raise ConnectorLoadError(f"{connector.__qualname__} lacks {attribute!r}")
        if connector.name in self._connectors:
            raise ConnectorLoadError(f"duplicate connector name {connector.name!r}")
        self._connectors[connector.name] = connector

    def load_entry_points(self, found: Iterable[EntryPoint] | None = None) -> None:
        """Carga los entry points; su nombre debe coincidir con `Connector.name`."""
        if found is None:
            found = entry_points(group=ENTRY_POINT_GROUP)
        for entry_point in found:
            try:
                connector = entry_point.load()
            except Exception as error:
                raise ConnectorLoadError(f"cannot load {entry_point.name!r}: {error}") from error
            self.register(connector)
            if connector.name != entry_point.name:
                del self._connectors[connector.name]
                raise ConnectorLoadError(
                    f"entry point {entry_point.name!r} loads connector {connector.name!r}"
                )

    def get(self, name: str) -> type[Connector]:
        try:
            return self._connectors[name]
        except KeyError:
            raise KeyError(f"unknown connector {name!r}") from None

    def names(self) -> list[str]:
        return sorted(self._connectors)
