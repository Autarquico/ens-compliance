import gzip
import hashlib
import io
import json
import tarfile
from datetime import UTC, datetime

import pytest
import yaml
from pydantic import SecretStr, ValidationError

from ens_evidence.kit.backups import (
    BackupInputError,
    BackupInputs,
    BundleSecrets,
    generate_repo_password,
    normalize_host_path,
    render_bundle,
    render_host_bundle,
    validation_messages,
)

NOW = datetime(2026, 11, 9, 10, 0, tzinfo=UTC)
SECRETS = BundleSecrets(
    repo_password=SecretStr("repo-pass-0123456789"),
    write_access_key_id=SecretStr("WRITEKEYID0001"),
    write_secret_access_key=SecretStr("write-secret-0001"),
    read_access_key_id=SecretStr("READKEYID0002"),
    read_secret_access_key=SecretStr("read-secret-0002"),
)


def inputs(**over):
    base = {
        "id": "caja",
        "asset_id": "vps-caja",
        "destination": {
            "endpoint": "https://s3.example.com",
            "bucket": "copias-pyme",
            "prefix": "caja",
            "lock_days": 30,
        },
        "schedule": {
            "cron": "0 2 * * *",
            "retention_days": 90,
            "max_backup_age_hours": 26,
            "max_check_age_days": 35,
            "restore_test_validity_days": 365,
        },
        "volumes": ["data"],
        "host_paths": ["/srv/datos"],
    }
    return BackupInputs.model_validate({**base, **over})


def unpack(blob):
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        return {m.name: (m, tar.extractfile(m).read()) for m in tar.getmembers()}


def test_bundle_is_deterministic():
    assert render_bundle(inputs(), SECRETS, NOW) == render_bundle(inputs(), SECRETS, NOW)


def test_bundle_has_exactly_the_contract_files_in_order():
    members = unpack(render_bundle(inputs(), SECRETS, NOW))
    assert list(members) == [
        "copias/config.json",
        "copias/compose.yaml",
        "copias/app.env",
        "copias/backups.yaml",
        "copias/MANIFEST",
    ]
    for member, _ in members.values():
        assert (member.uid, member.gid, member.uname, member.gname) == (0, 0, "", "")
        assert member.mtime == int(NOW.timestamp())


def test_gzip_header_carries_no_filename_or_wall_clock():
    blob = render_bundle(inputs(), SECRETS, NOW)
    assert gzip.decompress(blob)
    assert int.from_bytes(blob[4:8], "little") == int(NOW.timestamp())
    assert blob[3] == 0  # sin FNAME


def test_manifest_matches_files():
    members = unpack(render_bundle(inputs(), SECRETS, NOW))
    lines = members["copias/MANIFEST"][1].decode().splitlines()
    assert lines[0] == "format: ens-kit-backups/1"
    listed = {n: h for h, n in (line.removeprefix("sha256 ").split("  ") for line in lines[1:])}
    others = {n: b for n, (_, b) in members.items() if n != "copias/MANIFEST"}
    assert listed == {n: hashlib.sha256(b).hexdigest() for n, b in others.items()}


def test_config_json_is_backrest_format_without_users():
    config = json.loads(unpack(render_bundle(inputs(), SECRETS, NOW))["copias/config.json"][1])
    assert "auth" not in config
    repo = config["repos"][0]
    assert repo["uri"] == "s3:https://s3.example.com/copias-pyme/caja"
    assert repo["password"] == "repo-pass-0123456789"
    assert repo["env"] == [
        "AWS_ACCESS_KEY_ID=WRITEKEYID0001",
        "AWS_SECRET_ACCESS_KEY=write-secret-0001",
    ]
    assert {"prunePolicy", "checkPolicy"} <= set(repo)
    plan = config["plans"][0]
    assert plan["paths"] == ["/source/data", "/source/host/srv/datos"]
    assert plan["schedule"] == {"cron": "0 2 * * *"}
    assert plan["retention"] == {"policyTimeBucketed": {"daily": 90}}


def test_compose_override_mounts_read_only_only_on_backrest():
    override = yaml.safe_load(
        unpack(render_bundle(inputs(), SECRETS, NOW))["copias/compose.yaml"][1]
    )
    assert list(override) == ["services"]
    assert list(override["services"]) == ["backrest"]
    assert override["services"]["backrest"]["volumes"] == [
        "data:/source/data:ro",
        "/srv/datos:/source/host/srv/datos:ro",
    ]


def test_app_env_has_read_credentials_only():
    env = unpack(render_bundle(inputs(), SECRETS, NOW))["copias/app.env"][1].decode()
    assert env.splitlines() == [
        "ENS_BACKUPS_CAJA_RESTIC_PASSWORD=repo-pass-0123456789",
        "ENS_BACKUPS_CAJA_AWS_ACCESS_KEY_ID=READKEYID0002",
        "ENS_BACKUPS_CAJA_AWS_SECRET_ACCESS_KEY=read-secret-0002",
    ]
    assert "WRITE" not in env.upper().replace("WRITE_", "")


def test_backups_fragment_has_no_secret_and_expected_shape():
    members = unpack(render_bundle(inputs(), SECRETS, NOW))
    text = members["copias/backups.yaml"][1].decode()
    for value in (
        "repo-pass-0123456789",
        "WRITEKEYID0001",
        "write-secret-0001",
        "READKEYID0002",
        "read-secret-0002",
    ):
        assert value not in text
    assert yaml.safe_load(text) == {
        "repositories": [
            {
                "id": "caja",
                "asset_id": "vps-caja",
                "repository": "s3:https://s3.example.com/copias-pyme/caja",
                "max_backup_age_hours": 26,
                "max_check_age_days": 35,
                "restore_test_validity_days": 365,
                "min_check_read_data_percent": 5,
            }
        ]
    }


def test_min_check_read_data_percent_is_explicit_and_bounded():
    schedule = inputs().schedule.model_dump()
    chosen = inputs(schedule={**schedule, "min_check_read_data_percent": 0}).schedule
    assert chosen.min_check_read_data_percent == 0
    for bad in (-1, 101, 2.5):
        with pytest.raises(ValidationError):
            inputs(schedule={**schedule, "min_check_read_data_percent": bad})


@pytest.mark.parametrize("retention", [30, 10])
def test_retention_not_above_lock_is_rejected(retention):
    with pytest.raises(ValidationError) as exc:
        inputs(schedule={**inputs().schedule.model_dump(), "retention_days": retention})
    assert "debe ser mayor que el periodo de bloqueo" in validation_messages(exc.value)[0]


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/proc",
        "/proc/1",
        "/sys/kernel",
        "/dev",
        "/var/run/docker.sock",
        "/run/docker.sock",
        "/run",
        "/var/run",
        "/srv/../etc",
        "relativa/ruta",
        "/srv/a:b",
        "/srv/a\nb",
    ],
)
def test_forbidden_host_paths_are_rejected(path):
    with pytest.raises(ValueError):
        normalize_host_path(path)
    with pytest.raises(ValidationError):
        inputs(host_paths=[path])


def test_unknown_volume_and_empty_selection_rejected():
    with pytest.raises(ValidationError):
        inputs(volumes=["backrest-config"])
    with pytest.raises(ValidationError):
        inputs(volumes=[], host_paths=[])


def test_host_paths_are_normalized_and_deduplicated():
    assert inputs(host_paths=["/srv/datos/", "/srv//datos"]).host_paths == ("/srv/datos",)


@pytest.mark.parametrize(
    "destination",
    [
        {"endpoint": "http://s3.example.com"},
        {"endpoint": "https://user:pw@s3.example.com"},
        {"endpoint": "https://s3.example.com/bucket"},
        {"bucket": "A"},
        {"prefix": "../otro"},
    ],
)
def test_bad_destination_rejected(destination):
    base = inputs().destination.model_dump()
    with pytest.raises(ValidationError):
        inputs(destination={**base, **destination})


def test_write_and_read_credentials_must_differ():
    data = SECRETS.model_dump()
    data["read_access_key_id"] = SECRETS.write_access_key_id
    data["read_secret_access_key"] = SECRETS.write_secret_access_key
    with pytest.raises(ValidationError) as exc:
        BundleSecrets.model_validate(data)
    assert "distintas" in validation_messages(exc.value)[0]


def test_secrets_with_unsafe_characters_rejected_and_not_echoed():
    data = {**SECRETS.model_dump(), "repo_password": SecretStr("mal valor\nX=1")}
    with pytest.raises(ValidationError) as exc:
        BundleSecrets.model_validate(data)
    assert "mal valor" not in str(exc.value)
    assert "repo-pass" not in repr(SECRETS)


def test_generated_password_is_valid_and_random():
    first, second = generate_repo_password(), generate_repo_password()
    assert first != second
    BundleSecrets.model_validate({**SECRETS.model_dump(), "repo_password": first})


def test_naive_datetime_rejected():
    with pytest.raises(BackupInputError):
        render_bundle(inputs(), SECRETS, datetime(2026, 11, 9))


def test_host_bundle_has_own_repository_and_no_compose():
    host = inputs(
        id="servidor2",
        asset_id="srv-02",
        volumes=[],
        destination={**inputs().destination.model_dump(), "prefix": "servidor2"},
    )
    blob = render_host_bundle(host, SECRETS, NOW)
    assert blob == render_host_bundle(host, SECRETS, NOW)
    members = unpack(blob)
    assert list(members) == [
        "copias/config.json",
        "copias/app.env",
        "copias/backups.yaml",
        "copias/INSTRUCCIONES.md",
        "copias/MANIFEST",
    ]
    config = json.loads(members["copias/config.json"][1])
    assert config["plans"][0]["paths"] == ["/srv/datos"]
    assert config["repos"][0]["uri"].endswith("/copias-pyme/servidor2")
    assert "ENS_BACKUPS_SERVIDOR2_RESTIC_PASSWORD" in members["copias/app.env"][1].decode()
    instructions = members["copias/INSTRUCCIONES.md"][1].decode()
    for value in ("repo-pass-0123456789", "write-secret-0001", "WRITEKEYID0001"):
        assert value not in instructions


def test_host_bundle_rejects_box_volumes():
    with pytest.raises(BackupInputError):
        render_host_bundle(inputs(), SECRETS, NOW)
