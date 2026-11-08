import pytest

from ens_evidence.agent.secrets import (
    ENROLL_KEY_ENV,
    NODE_KEY_SECRET_ENV,
    AgentSecretsError,
    load_agent_secrets,
)

GOOD_A = "a" * 32
GOOD_B = "b" * 32


def env(**overrides):
    return {ENROLL_KEY_ENV: GOOD_A, NODE_KEY_SECRET_ENV: GOOD_B} | overrides


def test_loads_both_secrets():
    secrets = load_agent_secrets(env())
    assert secrets.enroll_key == GOOD_A.encode()
    assert secrets.node_key_secret == GOOD_B.encode()


@pytest.mark.parametrize("name", [ENROLL_KEY_ENV, NODE_KEY_SECRET_ENV])
def test_missing_secret_names_the_variable(name):
    values = env()
    del values[name]
    with pytest.raises(AgentSecretsError, match=name):
        load_agent_secrets(values)


@pytest.mark.parametrize("name", [ENROLL_KEY_ENV, NODE_KEY_SECRET_ENV])
def test_short_secret_is_rejected_without_showing_it(name):
    short = "s3cr3t-too-short"
    with pytest.raises(AgentSecretsError) as raised:
        load_agent_secrets(env(**{name: short}))
    assert name in str(raised.value)
    assert short not in str(raised.value)


def test_equal_secrets_are_rejected_without_showing_them():
    with pytest.raises(AgentSecretsError) as raised:
        load_agent_secrets(env(**{NODE_KEY_SECRET_ENV: GOOD_A}))
    assert GOOD_A not in str(raised.value)


def test_repr_and_str_are_masked():
    secrets = load_agent_secrets(env())
    for text in (repr(secrets), str(secrets), f"{secrets}"):
        assert GOOD_A not in text and GOOD_B not in text
