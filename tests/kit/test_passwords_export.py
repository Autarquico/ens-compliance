import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from ens_evidence.cli import app
from ens_evidence.kit import passwords_export
from ens_evidence.kit.passwords_export import (
    ExportedUser,
    PasswordsExport,
    PasswordsExportError,
    export,
    last_good_snapshot,
    parse_interval,
)

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
MIGRATION = "20261007120000"
SCHEMA = """
CREATE TABLE __diesel_schema_migrations (
  version VARCHAR(50) PRIMARY KEY NOT NULL, run_on TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE users (
  uuid TEXT NOT NULL PRIMARY KEY, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL,
  email TEXT NOT NULL UNIQUE, name TEXT NOT NULL, password_hash BLOB NOT NULL,
  enabled BOOLEAN NOT NULL DEFAULT 1);
CREATE TABLE twofactor (
  uuid TEXT NOT NULL PRIMARY KEY, user_uuid TEXT NOT NULL REFERENCES users (uuid),
  atype INTEGER NOT NULL, enabled BOOLEAN NOT NULL, data TEXT NOT NULL,
  last_used INTEGER NOT NULL DEFAULT 0, UNIQUE (user_uuid, atype));
CREATE TABLE devices (
  uuid TEXT NOT NULL, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL,
  user_uuid TEXT NOT NULL, name TEXT NOT NULL, atype INTEGER NOT NULL,
  refresh_token TEXT NOT NULL, PRIMARY KEY (uuid, user_uuid));
"""
ANA, BEA, CARLOS, DIANA, EVA = (f"00000000-0000-4000-8000-00000000000{n}" for n in range(1, 6))
SECRET = "refresh-token-ficticio"


def uid(n: int) -> str:
    return f"00000000-0000-4000-8000-0000000000{n:02d}"


class Db:
    def __init__(self, path: Path):
        self.conn = sqlite3.connect(path)
        self.conn.executescript(SCHEMA)
        self.conn.execute(
            "INSERT INTO __diesel_schema_migrations (version) VALUES (?)", (MIGRATION,)
        )

    def user(
        self,
        user_id,
        *,
        enabled=1,
        password_hash=b"\x01" * 32,
        created="2026-10-01 08:00:00.123456789",
    ):
        n = user_id[-2:]
        self.conn.execute(
            "INSERT INTO users VALUES (?,?,?,?,?,?,?)",
            (
                user_id,
                created,
                created,
                f"persona{n}@example.test",
                f"Persona {n}",
                password_hash,
                enabled,
            ),
        )

    def factor(self, user_id, atype, enabled=1):
        self.conn.execute(
            "INSERT INTO twofactor VALUES (?,?,?,?,?,0)",
            (f"tf-{user_id}-{atype}", user_id, atype, enabled, "otp-secreto-ficticio"),
        )

    def device(self, user_id, updated, name="d"):
        self.conn.execute(
            "INSERT INTO devices VALUES (?,?,?,?,?,0,?)",
            (f"{user_id}-{name}", updated, updated, user_id, name, SECRET),
        )

    def done(self):
        self.conn.commit()
        self.conn.close()


@pytest.fixture
def dirs(tmp_path):
    data, out, snap = tmp_path / "data", tmp_path / "out", tmp_path / "snap"
    data.mkdir()
    return data, out, snap


def build(data: Path, fill=lambda db: None) -> Path:
    path = data / "db.sqlite3"
    db = Db(path)
    fill(db)
    db.done()
    return path


def run(dirs, now=NOW):
    data, out, snap = dirs
    return export(data / "db.sqlite3", out, snap, now)


def published(dirs) -> dict:
    return json.loads((dirs[1] / "usuarios.json").read_bytes())


def by_id(result: PasswordsExport) -> dict:
    return {str(u.id): u for u in result.users}


def populate(db: Db):
    db.user(ANA)
    db.factor(ANA, 0)
    db.device(ANA, "2026-11-01 09:00:00.5")
    db.device(ANA, "2026-11-09 10:00:00.987654321", name="e")
    db.user(BEA)
    db.factor(BEA, 5)
    db.factor(BEA, 8)
    db.factor(BEA, 1000)
    db.user(CARLOS, password_hash=b"")
    db.user(DIANA)
    db.factor(DIANA, 3, enabled=0)
    db.user(EVA, enabled=0)
    db.factor(EVA, 7)


def test_second_factor_only_counts_enabled_real_types(dirs):
    build(dirs[0], populate)
    users = by_id(run(dirs))
    assert users[ANA].second_factor is True
    assert users[BEA].second_factor is False  # tipos 5, 8 y >=1000
    assert users[DIANA].second_factor is False  # enabled = false
    assert users[EVA].second_factor is True and users[EVA].enabled is False


@pytest.mark.parametrize("atype", [0, 1, 2, 3, 4, 6, 7])
def test_each_real_factor_type_counts(dirs, atype):
    def fill(db):
        db.user(ANA)
        db.factor(ANA, atype)

    build(dirs[0], fill)
    assert by_id(run(dirs))[ANA].second_factor is True


def test_invited_user_has_empty_hash(dirs):
    build(dirs[0], populate)
    users = by_id(run(dirs))
    assert users[CARLOS].invited is True
    assert users[ANA].invited is False


def test_last_active_is_latest_device_or_null(dirs):
    build(dirs[0], populate)
    users = by_id(run(dirs))
    assert users[ANA].last_active == datetime(2026, 11, 9, 10, 0, 0, 987654, tzinfo=UTC)
    assert users[BEA].last_active is None
    assert users[ANA].created_at == datetime(2026, 10, 1, 8, 0, 0, 123456, tzinfo=UTC)


def test_output_carries_neither_emails_nor_names_nor_secrets(dirs):
    build(dirs[0], populate)
    run(dirs)
    raw = (dirs[1] / "usuarios.json").read_bytes()
    for forbidden in (b"example.test", b"Persona", b"persona", SECRET.encode(), b"otp-secreto"):
        assert forbidden not in raw
    assert set(published(dirs)) == {
        "format",
        "exported_at",
        "vaultwarden_migration",
        "snapshot_ok",
        "last_good_snapshot_at",
        "users",
    }
    assert published(dirs)["format"] == 1
    assert published(dirs)["vaultwarden_migration"] == MIGRATION


def test_database_file_is_not_modified_even_with_a_live_wal(dirs):
    path = build(dirs[0])
    writer = sqlite3.connect(path)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute(
        "INSERT INTO users VALUES (?,?,?,?,?,?,1)",
        (ANA, "2026-10-01 08:00:00", "x", "ana@example.test", "Ana", b"\x01" * 32),
    )
    writer.commit()  # el dato vive solo en el WAL
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    result = run(dirs)
    assert [str(u.id) for u in result.users] == [ANA]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    writer.close()


def test_unknown_migration_publishes_nothing_and_keeps_previous_export(dirs):
    path = build(dirs[0], populate)
    run(dirs)
    previous = (dirs[1] / "usuarios.json").read_bytes()
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO __diesel_schema_migrations (version) VALUES ('29990101000000')")
    conn.commit()
    conn.close()
    with pytest.raises(PasswordsExportError, match="desconocida"):
        run(dirs, NOW + timedelta(minutes=15))
    assert (dirs[1] / "usuarios.json").read_bytes() == previous
    assert not [p for p in dirs[1].iterdir() if p.name != "usuarios.json"]


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE users SET created_at = 'ayer' WHERE uuid = '{ANA}'",
        "UPDATE users SET enabled = 2 WHERE uuid = '{ANA}'",
        "UPDATE users SET uuid = 'no-es-uuid' WHERE uuid = '{BEA}'",
        "UPDATE users SET password_hash = 'texto' WHERE uuid = '{ANA}'",
        "UPDATE devices SET updated_at = 12345",
        "UPDATE twofactor SET enabled = 'yes'",
        "UPDATE users SET created_at = '2027-01-01 00:00:00' WHERE uuid = '{ANA}'",
        "UPDATE devices SET updated_at = '2026-12-01 00:00:00'",
    ],
)
def test_corrupt_row_publishes_nothing_and_leaks_no_values(dirs, sql):
    path = build(dirs[0], populate)
    run(dirs)
    previous = (dirs[1] / "usuarios.json").read_bytes()
    conn = sqlite3.connect(path)
    conn.execute(sql.format(ANA=ANA, BEA=BEA))
    conn.commit()
    conn.close()
    with pytest.raises(PasswordsExportError) as caught:
        run(dirs, NOW + timedelta(minutes=15))
    for value in ("ayer", "no-es-uuid", "texto", "yes", "example.test", "2027-01-01", "2026-12-01"):
        assert value not in str(caught.value)
    assert (dirs[1] / "usuarios.json").read_bytes() == previous


def test_missing_database_publishes_nothing(dirs):
    with pytest.raises(PasswordsExportError):
        run(dirs)
    assert not dirs[1].exists()


def test_snapshot_is_published_with_date_and_passes_integrity(dirs):
    build(dirs[0], populate)
    (dirs[0] / "rsa_key.pem").write_text("clave ficticia")
    result = run(dirs)
    assert result.snapshot_ok is True and result.last_good_snapshot_at == NOW
    snap = dirs[2] / "db-20261111T120000Z.sqlite3"
    conn = sqlite3.connect(snap)
    assert conn.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    assert conn.execute("SELECT count(*) FROM users").fetchone() == (5,)
    conn.close()
    assert (dirs[2] / "rsa_key.pem").read_text() == "clave ficticia"
    assert not [p for p in dirs[2].iterdir() if p.name.startswith(".")]


def test_new_snapshot_replaces_the_previous_one(dirs):
    build(dirs[0], populate)
    run(dirs)
    run(dirs, NOW + timedelta(minutes=15))
    assert [p.name for p in dirs[2].glob("db-*")] == ["db-20261111T121500Z.sqlite3"]


def test_snapshot_failure_is_recorded_and_does_not_hide_users(dirs, monkeypatch):
    build(dirs[0], populate)
    run(dirs)
    monkeypatch.setattr(passwords_export, "_integrity_ok", lambda path: False)
    later = NOW + timedelta(minutes=15)
    result = run(dirs, later)
    assert result.snapshot_ok is False
    assert result.last_good_snapshot_at == NOW
    assert len(result.users) == 5
    data = published(dirs)
    assert data["snapshot_ok"] is False and data["last_good_snapshot_at"].startswith(
        "2026-11-11T12:00:00"
    )
    assert [p.name for p in dirs[2].glob("db-*")] == ["db-20261111T120000Z.sqlite3"]
    assert last_good_snapshot(dirs[2]) == NOW


def test_snapshot_failure_without_any_good_one_has_no_date(dirs, monkeypatch):
    build(dirs[0], populate)
    monkeypatch.setattr(passwords_export, "_integrity_ok", lambda path: False)
    result = run(dirs)
    assert result.snapshot_ok is False and result.last_good_snapshot_at is None


def base_export(**overrides) -> dict:
    user = {
        "id": ANA,
        "enabled": True,
        "invited": False,
        "created_at": "2026-10-01T08:00:00Z",
        "second_factor": True,
        "last_active": None,
    }
    data = {
        "format": 1,
        "exported_at": "2026-11-11T12:00:00Z",
        "vaultwarden_migration": MIGRATION,
        "snapshot_ok": True,
        "last_good_snapshot_at": "2026-11-11T12:00:00Z",
        "users": [user],
    }
    return data | overrides


def test_model_accepts_a_valid_export_and_is_frozen():
    model = PasswordsExport.model_validate_json(json.dumps(base_export()))
    with pytest.raises(ValidationError):
        model.format = 2  # type: ignore[misc]


@pytest.mark.parametrize(
    "overrides",
    [
        {"extra": 1},
        {"format": 2},
        {"format": True},
        {"format": "1"},
        {"exported_at": "2026-11-11T12:00:00"},
        {"exported_at": "2026-11-11T13:00:00+01:00"},
        {"snapshot_ok": "true"},
        {"snapshot_ok": True, "last_good_snapshot_at": None},
        {"last_good_snapshot_at": "2026-11-11T12:00:01Z"},
        {"users": [base_export()["users"][0]] * 2},
        {"users": [{**base_export()["users"][0], "email": "x@example.test"}]},
        {"users": [{**base_export()["users"][0], "enabled": 1}]},
        {"users": [{**base_export()["users"][0], "id": "123"}]},
        {"users": [{**base_export()["users"][0], "created_at": "2026-11-11T12:00:01Z"}]},
        {"users": [{**base_export()["users"][0], "last_active": "2026-11-12T00:00:00Z"}]},
    ],
)
def test_model_rejects(overrides):
    with pytest.raises(ValidationError):
        PasswordsExport.model_validate_json(json.dumps(base_export(**overrides)))


def test_model_dates_at_the_limit_are_accepted():
    user = {**base_export()["users"][0], "created_at": "2026-11-11T12:00:00Z"}
    PasswordsExport.model_validate_json(json.dumps(base_export(users=[user])))


def test_exported_user_requires_every_field():
    with pytest.raises(ValidationError):
        ExportedUser.model_validate({"id": ANA})


@pytest.mark.parametrize(("text", "seconds"), [("15m", 900), ("30s", 30), ("1h", 3600)])
def test_parse_interval(text, seconds):
    assert parse_interval(text) == seconds


@pytest.mark.parametrize("text", ["0m", "15", "m", "-5m", "1d", ""])
def test_parse_interval_rejects(text):
    with pytest.raises(ValueError):
        parse_interval(text)


def test_cli_one_shot_and_failure_exit_code(dirs):
    data, out, snap = dirs
    runner = CliRunner()
    args = [
        "kit",
        "contrasenas",
        "export",
        "--data",
        str(data),
        "--out",
        str(out),
        "--snapshot",
        str(snap),
    ]
    failed = runner.invoke(app, args)
    assert failed.exit_code == 1
    build(data, populate)
    done = runner.invoke(app, args)
    assert done.exit_code == 0, done.output
    assert len(published((data, out, snap))["users"]) == 5
    assert runner.invoke(app, [*args, "--every", "0m"]).exit_code == 2
