"""Errores de la copia independiente. Los mensajes nunca incluyen secretos."""

from ens_evidence.workspace import WorkspaceError


class BackupError(WorkspaceError):
    pass
