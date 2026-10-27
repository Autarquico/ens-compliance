"""Interfaz de conector (ADR 0003). Solo lectura: devuelve CollectionStatus y datos."""

from abc import ABC, abstractmethod
from typing import ClassVar

from pydantic import BaseModel

from ens_evidence.models.contracts import CollectionResult


class Connector(ABC):
    name: ClassVar[str]
    config_model: ClassVar[type[BaseModel]]

    @abstractmethod
    def collect(self, config: BaseModel) -> CollectionResult: ...
