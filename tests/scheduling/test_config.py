import datetime as dt

import pytest
import yaml

from ens_evidence.scheduling.config import (
    SCHEDULE_KEY,
    ScheduleConfig,
    ScheduleConfigError,
    default_schedule_yaml,
    load_schedule_config,
)
from ens_evidence.workspace import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(tmp_path)


def write(store, text: str) -> None:
    store.write_text(SCHEDULE_KEY, text)


def test_missing_file_gives_documented_defaults_marked_as_default(store):
    loaded = load_schedule_config(store)
    cfg = loaded.config
    assert loaded.is_default
    assert cfg.timezone == "Europe/Madrid"
    assert cfg.daily.time == dt.time(6, 0)
    assert cfg.monthly.day == 1
    assert cfg.collect.timeout_minutes == 15
    assert (cfg.collect.retries, cfg.collect.retry_delay_minutes) == (2, [5, 15])
    assert (cfg.cycle.attempts, cfg.cycle.retry_interval_minutes) == (3, 30)
    assert cfg.backup.enabled is True
    assert cfg.expiry_warnings.technical_evidence == 7
    assert cfg.expiry_warnings.manual_evidence == 14


def test_init_config_yaml_loads_and_equals_defaults(store):
    write(store, default_schedule_yaml())
    loaded = load_schedule_config(store)
    assert not loaded.is_default
    assert loaded.config == ScheduleConfig()


def test_partial_file_overrides_only_what_it_says(store):
    write(store, 'daily:\n  time: "02:30"\nbackup:\n  enabled: false\n')
    cfg = load_schedule_config(store).config
    assert cfg.daily.time == dt.time(2, 30)
    assert cfg.backup.enabled is False
    assert cfg.timezone == "Europe/Madrid"


def test_empty_file_is_defaults_but_not_marked_default(store):
    write(store, "")
    loaded = load_schedule_config(store)
    assert loaded.config == ScheduleConfig() and not loaded.is_default


def test_delay_repeats_the_last_value(store):
    write(store, "collect:\n  retries: 4\n  retry_delay_minutes: [1, 2]\n")
    collect = load_schedule_config(store).config.collect
    assert [collect.delay_for(n) for n in (1, 2, 3, 4)] == [1, 2, 2, 2]


@pytest.mark.parametrize(
    "text",
    [
        "timezone: Mars/Olympus\n",
        "timezone: ../etc/passwd\n",
        "timezone: ''\n",
        "daily:\n  time: 10:30\n",  # YAML lo lee como entero
        'daily:\n  time: "25:00"\n',
        'daily:\n  time: "6:00"\n',
        "monthly:\n  day: 0\n",
        "monthly:\n  day: 29\n",
        "collect:\n  timeout_minutes: 0\n",
        "collect:\n  retries: -1\n",
        "collect:\n  connectors: ['../x']\n",
        "backup:\n  enabled: maybe\n",
        "unknown: 1\n",
        "daily:\n  extra: 1\n",
        "- a\n- b\n",
        "daily: {time: '06:00'\ndaily: x\n",
        "daily:\n  time: '06:00'\ndaily:\n  time: '07:00'\n",
        "\x00bad: [",
    ],
)
def test_invalid_config_raises_config_error(store, text):
    write(store, text)
    with pytest.raises(ScheduleConfigError) as err:
        load_schedule_config(store)
    assert SCHEDULE_KEY in str(err.value)


def test_unknown_zone_error_names_the_field_not_the_value(store):
    write(store, "timezone: Mars/Olympus\n")
    with pytest.raises(ScheduleConfigError) as err:
        load_schedule_config(store)
    assert "timezone" in str(err.value)
    assert "Mars" not in str(err.value)


@pytest.mark.parametrize(
    "text",
    [
        "collect:\n  api_key: abc\n",
        "token: abc\n",
        "backup:\n  password: hunter2\n",
        "collect:\n  connectors: [a]\n  AWS_SECRET_ACCESS_KEY: x\n",
        "credentials:\n  user: x\n",
    ],
)
def test_credential_keys_are_rejected_without_echoing_values(store, text):
    write(store, text)
    with pytest.raises(ScheduleConfigError) as err:
        load_schedule_config(store)
    assert "abc" not in str(err.value) and "hunter2" not in str(err.value)


def test_secret_looking_values_are_rejected(store):
    leaked = "ghp_" + "a" * 36
    write(store, f"collect:\n  connectors: [{leaked}]\n")
    with pytest.raises(ScheduleConfigError) as err:
        load_schedule_config(store)
    assert leaked not in str(err.value)


def test_non_utf8_file_is_a_config_error(store, tmp_path):
    (tmp_path / "reports").mkdir()
    (tmp_path / SCHEDULE_KEY).write_bytes(b"\xff\xfe\x00")
    with pytest.raises(ScheduleConfigError):
        load_schedule_config(store)


def test_default_yaml_is_valid_yaml_with_documented_keys():
    data = yaml.safe_load(default_schedule_yaml())
    assert set(data) == {
        "timezone",
        "daily",
        "monthly",
        "collect",
        "cycle",
        "expiry_warnings",
        "backup",
    }
    assert data["daily"]["time"] == "06:00"


def test_zone_property_resolves_tzdata():
    assert ScheduleConfig().zone.key == "Europe/Madrid"


# --- límites de recarga y de duración ----------------------------------------------------------


def test_yaml_aliases_are_rejected_before_any_expansion(store):
    bomb = "a: &a [x, x, x, x, x, x, x, x, x, x]\n" + "".join(
        f"b{i}: &b{i} [{', '.join([f'*{p}' for p in ('a' if i == 0 else f'b{i - 1}',)] * 10)}]\n"
        for i in range(8)
    )
    write(store, bomb)
    with pytest.raises(ScheduleConfigError, match="alias"):
        load_schedule_config(store)


def test_an_oversized_file_is_rejected(store):
    write(store, "# " + "x" * 70_000 + "\ntimezone: Europe/Madrid\n")
    with pytest.raises(ScheduleConfigError, match="KiB"):
        load_schedule_config(store)


def test_worst_case_of_a_collection_is_bounded(store):
    write(
        store,
        "collect:\n  retries: 5\n  timeout_minutes: 240\n  retry_delay_minutes: [240]\n",
    )
    with pytest.raises(ScheduleConfigError, match="peor caso"):
        load_schedule_config(store)


def test_default_worst_case_is_far_below_the_bound():
    assert ScheduleConfig().collect.worst_case_minutes == 65


def test_backup_timeout_is_configurable_and_bounded(store):
    write(store, "backup:\n  timeout_minutes: 30\n")
    assert load_schedule_config(store).config.backup.timeout_minutes == 30
    write(store, "backup:\n  timeout_minutes: 100000\n")
    with pytest.raises(ScheduleConfigError):
        load_schedule_config(store)


def test_an_empty_retry_delay_list_is_rejected(store):
    write(store, "collect:\n  retry_delay_minutes: []\n")
    with pytest.raises(ScheduleConfigError):
        load_schedule_config(store)
