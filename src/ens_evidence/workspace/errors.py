"""Errores tipados del workspace. Los mensajes nunca incluyen valores rechazados."""


class WorkspaceError(Exception):
    """Base de los errores del workspace."""


class InvalidKey(WorkspaceError, ValueError):
    pass


class NotFound(WorkspaceError):
    pass


class NotText(WorkspaceError):
    pass


class AccessDenied(WorkspaceError):
    pass


class StorageUnavailable(WorkspaceError):
    pass


class SettingsError(WorkspaceError):
    pass
