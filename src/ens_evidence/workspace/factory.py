"""Construye el `WorkspaceStore` descrito por los ajustes."""

from ens_evidence.workspace.local import LocalStore
from ens_evidence.workspace.s3 import S3Store
from ens_evidence.workspace.settings import Settings
from ens_evidence.workspace.store import WorkspaceStore


def open_store(settings: Settings) -> WorkspaceStore:
    workspace = settings.workspace
    if workspace.backend == "s3":
        s3 = workspace.s3
        assert s3 is not None
        return S3Store(s3.bucket, s3.prefix, endpoint_url=s3.endpoint_url, region=s3.region)
    assert workspace.local is not None
    return LocalStore(workspace.local.path)
