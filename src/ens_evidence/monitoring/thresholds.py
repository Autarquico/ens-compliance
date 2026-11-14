"""Umbrales (`monitoring/thresholds.yaml`); sin fichero o inválido, los de fábrica."""

import logging

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ens_evidence.workspace import WorkspaceError, WorkspaceStore
from ens_evidence.yaml_unique import safe_load_unique

THRESHOLDS_KEY = "monitoring/thresholds.yaml"
_LOGGER = logging.getLogger("ens_evidence.monitoring")


class Thresholds(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    disk_percent: float = Field(default=85, gt=0, le=100)
    memory_percent: float = Field(default=90, gt=0, le=100)
    load_per_core: float = Field(default=2.0, gt=0)
    tls_days: int = Field(default=21, ge=1)
    agent_silence_hours: float = Field(default=3, gt=0)
    backup_hours: float = Field(default=26, gt=0)


def load_thresholds(store: WorkspaceStore) -> Thresholds:
    try:
        raw = safe_load_unique(store.read_text(THRESHOLDS_KEY))
        return Thresholds.model_validate(raw or {})
    except (WorkspaceError, yaml.YAMLError, ValidationError):
        if store.exists(THRESHOLDS_KEY):
            _LOGGER.warning("%s no es válido; se usan los umbrales por defecto", THRESHOLDS_KEY)
        return Thresholds()
