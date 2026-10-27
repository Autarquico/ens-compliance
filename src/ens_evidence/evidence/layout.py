"""Claves de las evidencias dentro del workspace (ADR 0008)."""

import hashlib
import re
import secrets
from datetime import datetime

EVIDENCE_ROOT = "evidence"
RAW_PREFIX = f"{EVIDENCE_ROOT}/raw"
MANIFESTS_PREFIX = f"{EVIDENCE_ROOT}/manifests"
REVIEWS_PREFIX = f"{EVIDENCE_ROOT}/reviews"
BACKUPS_PREFIX = f"{EVIDENCE_ROOT}/backups"
# Reservado a la copia: nunca se importa al workspace.
BACKUP_META_PREFIX = ".ens-backup"

_ID_PATTERN = re.compile(r"^EV-\d{8}-[0-9a-f]{8}$")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def new_evidence_id(now: datetime) -> str:
    return f"EV-{now:%Y%m%d}-{secrets.token_hex(4)}"


def is_evidence_id(value: str) -> bool:
    return _ID_PATTERN.fullmatch(value) is not None


def artifact_key(sha256: str) -> str:
    return f"{RAW_PREFIX}/{sha256}"


def manifest_key(evidence_id: str) -> str:
    return f"{MANIFESTS_PREFIX}/{evidence_id}.yaml"


def reviews_prefix(evidence_id: str) -> str:
    return f"{REVIEWS_PREFIX}/{evidence_id}"
