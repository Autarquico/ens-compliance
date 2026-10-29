import pytest

from ens_evidence.connectors.restic import ResticConnector
from ens_evidence.workspace.settings import BackupSettings

from .fake_restic import NOW, REPOSITORY, FakeRestic
from .record_data import restic_run


@pytest.fixture
def restic() -> FakeRestic:
    return FakeRestic()


@pytest.fixture
def connector(restic) -> ResticConnector:
    return ResticConnector(
        backup=BackupSettings(restic_repository=REPOSITORY), executor=restic, clock=lambda: NOW
    )


@pytest.fixture
def collect(connector):
    """Recoge sobre el ejecutor falso con los registros dados."""

    def run(*, offsite: bool = True, unreadable: dict | None = None, **records):
        return connector.collect(restic_run(offsite=offsite, unreadable=unreadable, **records))

    return run


@pytest.fixture
def section(collect):
    def get(name: str, **records) -> dict:
        return collect(**records).data["sections"][name]

    return get
