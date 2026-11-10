from datetime import UTC, datetime, timedelta

import pytest
import yaml
from pydantic import ValidationError

from ens_evidence.kit.custody import (
    CUSTODIANS_PREFIX,
    CustodianRegisterError,
    latest_register,
    record_custodians,
)
from ens_evidence.workspace import LocalStore

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


def declaration(**overrides) -> bytes:
    data = {
        "custodians": [
            {"name": "Ana Pérez", "since": "2026-11-01T00:00:00+00:00"},
            {"name": "Luis Gómez", "since": "2026-11-01T00:00:00+00:00"},
        ],
        "key_shares": 3,
        "key_threshold": 2,
        "review": {
            "reviewer": "Marta Ruiz",
            "role": "security_officer",
            "reviewed_at": "2026-11-09T10:00:00+00:00",
            "valid_until": "2027-11-09T10:00:00+00:00",
        },
        **overrides,
    }
    return yaml.safe_dump(data).encode()


@pytest.fixture
def store(tmp_path):
    return LocalStore(tmp_path / "ws")


def test_guarda_el_registro_y_la_evidencia_manual(store):
    record_custodians(store, declaration(), NOW)

    assert len(store.list(CUSTODIANS_PREFIX)) == 1
    register, unreadable = latest_register(store, NOW)
    assert register is not None and register.key_threshold == 2 and unreadable == []
    assert any(k.endswith(".yaml") or k.endswith(".json") for k in store.list("evidence"))


def test_no_sobrescribe(store):
    record_custodians(store, declaration(), NOW)
    with pytest.raises(CustodianRegisterError):
        record_custodians(store, declaration(), NOW)


@pytest.mark.parametrize("name", ["ana@example.com", "12345678Z", "", "x" * 500])
def test_rechaza_nombres_que_no_son_nombres(store, name):
    custodians = [{"name": name, "since": "2026-11-01T00:00:00+00:00"}]
    with pytest.raises(ValidationError):
        record_custodians(store, declaration(custodians=custodians), NOW)
    assert store.list(CUSTODIANS_PREFIX) == []


def test_rechaza_umbral_mayor_que_partes(store):
    with pytest.raises(ValidationError):
        record_custodians(store, declaration(key_shares=2, key_threshold=3), NOW)


def test_rechaza_campos_extra_como_las_partes(store):
    with pytest.raises(ValidationError):
        record_custodians(store, declaration(shares=["abc"]), NOW)


def test_rechaza_revision_en_el_futuro(store):
    with pytest.raises(CustodianRegisterError):
        record_custodians(store, declaration(), NOW - timedelta(days=30))


def test_sin_registro_vigente_devuelve_none(store):
    record_custodians(store, declaration(), NOW)
    assert latest_register(store, NOW + timedelta(days=400))[0] is None


def test_devuelve_el_mas_reciente_y_lista_ilegibles(store):
    record_custodians(store, declaration(key_shares=3), NOW)
    newer = yaml.safe_load(declaration(key_shares=5))
    newer["review"]["reviewed_at"] = "2026-11-10T09:00:00+00:00"
    record_custodians(store, yaml.safe_dump(newer).encode(), NOW + timedelta(seconds=1))
    store.create_bytes(f"{CUSTODIANS_PREFIX}/roto.yaml", b"{{{")

    register, unreadable = latest_register(store, NOW + timedelta(days=1))

    assert register is not None and register.key_shares == 5
    assert unreadable == [f"{CUSTODIANS_PREFIX}/roto.yaml"]
