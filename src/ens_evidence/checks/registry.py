"""Qué conectores ejecuta el motor, con su configuración y sus checks.

Se construye sobre `checks/catalog.py`: los ids y el conector propietario salen de ahí. Solo
importa configuraciones y funciones de check (puras); nunca los conectores, así que el motor no
puede recoger datos ni cargar entry points.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType

from pydantic import BaseModel

from ens_evidence.checks.catalog import CHECKS
from ens_evidence.connectors.backups import checks as backups
from ens_evidence.connectors.backups.config import BackupsConfig
from ens_evidence.connectors.cloudflare import checks as cloudflare
from ens_evidence.connectors.cloudflare.config import CloudflareConfig
from ens_evidence.connectors.github import checks as github
from ens_evidence.connectors.github.config import GitHubConfig
from ens_evidence.connectors.google_workspace import checks as workspace
from ens_evidence.connectors.google_workspace.config import GoogleWorkspaceConfig
from ens_evidence.connectors.keystore import checks as keystore
from ens_evidence.connectors.keystore.config import KeystoreConfig
from ens_evidence.connectors.restic import checks as restic
from ens_evidence.connectors.restic.config import ResticConfig
from ens_evidence.endpoint.config import CONFIG_NAME as ENDPOINT
from ens_evidence.endpoint.config import EndpointConfig
from ens_evidence.models.contracts import CheckResult, CollectionResult

CheckFunction = Callable[[CollectionResult, str, datetime], list[CheckResult]]

CONFIG_MODELS: Mapping[str, type[BaseModel]] = MappingProxyType(
    {
        "github": GitHubConfig,
        "cloudflare": CloudflareConfig,
        "google-workspace": GoogleWorkspaceConfig,
        "restic": ResticConfig,
        "backups": BackupsConfig,
        "keystore": KeystoreConfig,
        ENDPOINT: EndpointConfig,
    }
)

CHECK_FUNCTIONS: Mapping[str, CheckFunction] = MappingProxyType(
    {
        github.ORG_2FA: github.org_2fa_required,
        github.BRANCH_PROTECTED: github.default_branch_protected,
        github.SECRET_SCANNING: github.secret_scanning_enabled,
        github.DEPENDABOT: github.dependabot_alerts_enabled,
        cloudflare.CHECK_ID: cloudflare.tunnel_requires_access,
        workspace.USER_2SV: workspace.user_2sv_enrolled,
        workspace.ADMIN_2SV: workspace.admin_2sv_enforced,
        workspace.SUSPENDED_NO_ADMIN: workspace.suspended_without_admin_role,
        restic.RECENT: restic.backup_001_recent,
        restic.NO_ERRORS: restic.backup_002_no_errors,
        restic.REPOSITORY_ACCESSIBLE: restic.backup_003_repository_accessible,
        restic.INTEGRITY: restic.backup_004_integrity,
        restic.RESTORE_TEST: restic.backup_005_restore_test,
        restic.OFFSITE: restic.backup_006_offsite,
        backups.RECENT: backups.backup_001_recent,
        backups.REPOSITORY_ACCESSIBLE: backups.backup_003_repository_accessible,
        backups.INTEGRITY: backups.backup_004_integrity,
        backups.RESTORE_TEST: backups.backup_005_restore_test,
        backups.OFFSITE: backups.backup_006_offsite,
        keystore.UNSEALED: keystore.unsealed,
        keystore.AUDIT_ENABLED: keystore.audit_enabled,
        keystore.KEYS_ROTATED: keystore.keys_rotated,
        keystore.NO_ROOT_TOKEN: keystore.no_root_token,
        keystore.APP_TOKEN_SCOPED: keystore.app_token_scoped,
        keystore.CUSTODIANS_REGISTERED: keystore.custodians_registered,
    }
)


@dataclass(frozen=True)
class EngineConnector:
    """`checks`: función por id; vacío en `endpoint`, que se evalúa en bloque con su evaluador."""

    name: str
    config_model: type[BaseModel]
    check_ids: tuple[str, ...]
    checks: Mapping[str, CheckFunction]


def _engine_connector(name: str) -> EngineConnector:
    ids = tuple(check_id for check_id, spec in CHECKS.items() if spec.connector == name)
    functions = {} if name == ENDPOINT else {i: CHECK_FUNCTIONS[i] for i in ids}
    return EngineConnector(name, CONFIG_MODELS[name], ids, functions)


ENGINE_CONNECTORS: Mapping[str, EngineConnector] = MappingProxyType(
    {name: _engine_connector(name) for name in sorted({s.connector for s in CHECKS.values()})}
)
