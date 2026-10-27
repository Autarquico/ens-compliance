from datetime import UTC, datetime, timedelta

import pytest

NOW = datetime(2026, 6, 1, tzinfo=UTC)
SHA = "a" * 64


@pytest.fixture
def now() -> datetime:
    return NOW


@pytest.fixture
def future() -> datetime:
    return NOW + timedelta(days=90)


@pytest.fixture
def past() -> datetime:
    return NOW - timedelta(days=1)
