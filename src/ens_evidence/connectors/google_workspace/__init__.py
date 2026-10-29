"""Collector de Google Workspace (solo lectura)."""

from ens_evidence.connectors.google_workspace.config import GoogleWorkspaceConfig
from ens_evidence.connectors.google_workspace.connector import (
    KEY_ENV,
    GoogleWorkspaceConnector,
)

__all__ = ["KEY_ENV", "GoogleWorkspaceConfig", "GoogleWorkspaceConnector"]
