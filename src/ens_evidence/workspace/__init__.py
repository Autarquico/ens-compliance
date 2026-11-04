"""Workspace de una organización: ajustes y backends de almacenamiento (ADR 0006)."""

from ens_evidence.workspace.errors import (
    AccessDenied,
    AlreadyExists,
    ConditionalWriteUnsupported,
    InvalidKey,
    NotFound,
    NotText,
    SettingsError,
    StorageUnavailable,
    WorkspaceError,
)
from ens_evidence.workspace.factory import open_store
from ens_evidence.workspace.keys import normalize_key
from ens_evidence.workspace.local import LocalStore
from ens_evidence.workspace.s3 import S3Store
from ens_evidence.workspace.settings import (
    BackupSettings,
    PreAuditSettings,
    Settings,
    is_inside_product,
    load_settings,
    resolve_settings_path,
)
from ens_evidence.workspace.store import WorkspaceStore

__all__ = [
    "AccessDenied",
    "BackupSettings",
    "PreAuditSettings",
    "AlreadyExists",
    "ConditionalWriteUnsupported",
    "InvalidKey",
    "LocalStore",
    "NotFound",
    "NotText",
    "S3Store",
    "Settings",
    "SettingsError",
    "StorageUnavailable",
    "WorkspaceError",
    "WorkspaceStore",
    "is_inside_product",
    "load_settings",
    "normalize_key",
    "open_store",
    "resolve_settings_path",
]
