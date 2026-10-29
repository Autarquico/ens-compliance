import os
import shutil
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


@pytest.fixture(scope="session")
def real_restic():
    """restic real; ausente = skip en local y fallo en CI."""
    if shutil.which("restic") is not None:
        return
    message = "restic no está instalado (brew install restic / apt-get install restic)"
    if os.environ.get("CI") == "true":
        pytest.fail(message)
    pytest.skip(message)
