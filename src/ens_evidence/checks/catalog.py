"""Catálogo neutral de checks: id, conector propietario y política de sujetos.

Módulo hoja, sin dependencias del paquete: lo leen la matriz (qué checks existen y quién los
ejecuta) y el motor (qué sujetos debe cubrir cada uno). No hay un segundo catálogo. Un test lo
compara con las constantes de cada `checks.py`.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

ENDPOINT_CONNECTOR = "endpoint"


class SubjectKind(StrEnum):
    """Qué sujetos debe cubrir un check."""

    ORGANIZATION = "organization"
    REPOSITORY = "repository"
    HOST = "host"
    WORKSTATION = "workstation"  # solo laptops: puestos de usuario, nunca servidores
    SELF_REPORTED = "self_reported"  # sin sujetos de inventario: la completitud la decide el check


@dataclass(frozen=True)
class CheckSpec:
    connector: str
    subjects: SubjectKind
    skip_archived: bool = False


CHECKS: Mapping[str, CheckSpec] = MappingProxyType(
    {
        "github.org_2fa_required": CheckSpec("github", SubjectKind.ORGANIZATION),
        "github.default_branch_protected": CheckSpec(
            "github", SubjectKind.REPOSITORY, skip_archived=True
        ),
        "github.secret_scanning_enabled": CheckSpec(
            "github", SubjectKind.REPOSITORY, skip_archived=True
        ),
        "github.dependabot_alerts_enabled": CheckSpec(
            "github", SubjectKind.REPOSITORY, skip_archived=True
        ),
        "cloudflare.tunnel_requires_access": CheckSpec("cloudflare", SubjectKind.SELF_REPORTED),
        "workspace.user_2sv_enrolled": CheckSpec("google-workspace", SubjectKind.SELF_REPORTED),
        "workspace.admin_2sv_enforced": CheckSpec("google-workspace", SubjectKind.SELF_REPORTED),
        "workspace.suspended_without_admin_role": CheckSpec(
            "google-workspace", SubjectKind.SELF_REPORTED
        ),
        "restic.backup_001_recent": CheckSpec("restic", SubjectKind.SELF_REPORTED),
        "restic.backup_002_no_errors": CheckSpec("restic", SubjectKind.SELF_REPORTED),
        "restic.backup_003_repository_accessible": CheckSpec("restic", SubjectKind.SELF_REPORTED),
        "restic.backup_004_integrity": CheckSpec("restic", SubjectKind.SELF_REPORTED),
        "restic.backup_005_restore_test": CheckSpec("restic", SubjectKind.SELF_REPORTED),
        "restic.backup_006_offsite": CheckSpec("restic", SubjectKind.SELF_REPORTED),
        "backups.backup_001_recent": CheckSpec("backups", SubjectKind.SELF_REPORTED),
        "backups.backup_003_repository_accessible": CheckSpec("backups", SubjectKind.SELF_REPORTED),
        "backups.backup_004_integrity": CheckSpec("backups", SubjectKind.SELF_REPORTED),
        "backups.backup_005_restore_test": CheckSpec("backups", SubjectKind.SELF_REPORTED),
        "backups.backup_006_offsite": CheckSpec("backups", SubjectKind.SELF_REPORTED),
        "keystore.unsealed": CheckSpec("keystore", SubjectKind.SELF_REPORTED),
        "keystore.audit_enabled": CheckSpec("keystore", SubjectKind.SELF_REPORTED),
        "keystore.keys_rotated": CheckSpec("keystore", SubjectKind.SELF_REPORTED),
        "keystore.no_root_token": CheckSpec("keystore", SubjectKind.SELF_REPORTED),
        "keystore.app_token_scoped": CheckSpec("keystore", SubjectKind.SELF_REPORTED),
        "keystore.custodians_registered": CheckSpec("keystore", SubjectKind.SELF_REPORTED),
        "passwords.second_factor": CheckSpec("passwords", SubjectKind.SELF_REPORTED),
        "passwords.no_inactive_accounts": CheckSpec("passwords", SubjectKind.SELF_REPORTED),
        "passwords.snapshot_recent": CheckSpec("passwords", SubjectKind.SELF_REPORTED),
        "HOST-001": CheckSpec(ENDPOINT_CONNECTOR, SubjectKind.HOST),
        "HOST-002": CheckSpec(ENDPOINT_CONNECTOR, SubjectKind.HOST),
        "HOST-003": CheckSpec(ENDPOINT_CONNECTOR, SubjectKind.HOST),
        "HOST-004": CheckSpec(ENDPOINT_CONNECTOR, SubjectKind.HOST),
        "HOST-005": CheckSpec(ENDPOINT_CONNECTOR, SubjectKind.HOST),
        "HOST-006": CheckSpec(ENDPOINT_CONNECTOR, SubjectKind.HOST),
        "HOST-007": CheckSpec(ENDPOINT_CONNECTOR, SubjectKind.HOST),
        "HOST-008": CheckSpec(ENDPOINT_CONNECTOR, SubjectKind.HOST),
        "HOST-009": CheckSpec(ENDPOINT_CONNECTOR, SubjectKind.WORKSTATION),
    }
)

KNOWN_CHECKS: Mapping[str, str] = MappingProxyType(
    {check_id: spec.connector for check_id, spec in CHECKS.items()}
)
