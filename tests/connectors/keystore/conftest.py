import pytest

from ens_evidence.connectors.keystore import KeystoreConnector, KeystoreRun
from ens_evidence.connectors.keystore.connector import TOKEN_ENV
from ens_evidence.kit.custody import CustodianRegister

from .fake_openbao import NOW, TOKEN, FakeOpenBao

REGISTER = CustodianRegister.model_validate(
    {
        "custodians": [
            {"name": "Ana Pérez", "since": "2026-11-01T00:00:00+00:00"},
            {"name": "Luis Gómez", "since": "2026-11-01T00:00:00+00:00"},
            {"name": "Marta Ruiz", "since": "2026-11-01T00:00:00+00:00"},
        ],
        "key_shares": 3,
        "key_threshold": 2,
        "review": {
            "reviewer": "Marta Ruiz",
            "role": "security_officer",
            "reviewed_at": "2026-11-09T10:00:00+00:00",
            "valid_until": "2027-11-09T10:00:00+00:00",
        },
    }
)


@pytest.fixture
def api() -> FakeOpenBao:
    return FakeOpenBao()


@pytest.fixture
def config() -> KeystoreRun:
    return KeystoreRun(asset_id="openbao", max_key_age_days=90, custody=REGISTER)


@pytest.fixture
def collect(api, config):
    def run(env=None, **changes):
        env = {TOKEN_ENV: TOKEN} if env is None else env
        connector = KeystoreConnector(transport=api.transport, clock=lambda: NOW, env=env)
        return connector.collect(config.model_copy(update=changes))

    return run
