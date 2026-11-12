import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from ens_evidence.connectors.passwords import PasswordsConfig, PasswordsConnector

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)


def iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None


def days_ago(days: float) -> datetime:
    return NOW - timedelta(days=days)


def user(**over) -> dict:
    base = {
        "id": str(uuid.uuid4()),
        "enabled": True,
        "invited": False,
        "created_at": iso(days_ago(200)),
        "second_factor": True,
        "last_active": iso(days_ago(2)),
    }
    return {**base, **over}


def export_doc(users=None, **over) -> dict:
    base = {
        "format": 1,
        "exported_at": iso(NOW - timedelta(minutes=5)),
        "vaultwarden_migration": "20261007120000",
        "snapshot_ok": True,
        "last_good_snapshot_at": iso(NOW - timedelta(minutes=5)),
        "users": [user()] if users is None else users,
    }
    return {**base, **over}


@pytest.fixture
def export_file(tmp_path):
    path = tmp_path / "usuarios.json"

    def write(doc=None, raw: bytes | None = None):
        path.write_bytes(raw if raw is not None else json.dumps(doc or export_doc()).encode())
        return path

    write.path = path
    return write


@pytest.fixture
def collect(export_file):
    def run(doc=None, raw=None, **changes):
        if doc is not None or raw is not None:
            export_file(doc, raw)
        config = PasswordsConfig(
            asset_id="vaultwarden",
            export_path=str(export_file.path),
            inactive_days=90,
            max_export_age_minutes=60,
        ).model_copy(update=changes)
        return PasswordsConnector(clock=lambda: NOW).collect(config)

    return run
