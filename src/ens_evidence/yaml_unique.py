"""Lectura YAML estricta para los datos del workspace."""

from collections.abc import Hashable
from typing import Any

import yaml


class _UniqueKeysLoader(yaml.SafeLoader):
    """`safe_load` que rechaza las claves repetidas, también anidadas: PyYAML se queda con la
    última y un valor favorable añadido al final taparía el desfavorable que había antes."""

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        self.flatten_mapping(node)
        seen: set[Any] = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=True)
            if isinstance(key, Hashable):
                if key in seen:
                    raise yaml.constructor.ConstructorError(
                        None, None, f"clave duplicada: {key!r}", key_node.start_mark
                    )
                seen.add(key)
        return super().construct_mapping(node, deep)


def safe_load_unique(text: str) -> Any:
    """`yaml.safe_load` que lanza `yaml.YAMLError` si algún mapa repite una clave."""
    return yaml.load(text, Loader=_UniqueKeysLoader)  # noqa: S506 - SafeLoader sin constructores
