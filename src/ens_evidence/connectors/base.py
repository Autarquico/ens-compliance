"""Interfaz de conector (ADR 0003). Solo lectura: devuelve CollectionStatus y datos."""

from abc import ABC, abstractmethod
from typing import ClassVar, Self

from pydantic import BaseModel

from ens_evidence.models.contracts import CollectionResult
from ens_evidence.workspace import WorkspaceStore
from ens_evidence.workspace.settings import Settings


class Connector(ABC):
    name: ClassVar[str]
    config_model: ClassVar[type[BaseModel]]

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        """Construye el conector con los ajustes con los que se abrió el workspace."""
        return cls()

    @abstractmethod
    def collect(self, config: BaseModel) -> CollectionResult: ...

    def prepare(self, config: BaseModel, store: WorkspaceStore) -> BaseModel:
        """Completa la configuración con datos del workspace antes de recoger."""
        return config
