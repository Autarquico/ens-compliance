import pytest

from ens_evidence.incidents import (
    RecordKind,
    check_incident_payload,
    record_incident,
)
from ens_evidence.incidents.privacy import check_declared_name

from .helpers import RECORDED, full_payload
from .test_privacy import FLAT, NESTED, dotted, put
from .test_store import rejection
from .variants import ACCEPTED, REJECTED

LABELS = list(REJECTED)


@pytest.mark.parametrize("label", LABELS)
@pytest.mark.parametrize("path", [FLAT, *NESTED], ids=dotted)
def test_variant_is_rejected_in_free_and_nested_fields(label, path):
    value = f"Aviso {REJECTED[label]} fin"
    errors, _ = check_incident_payload(put(full_payload(), path, value))
    assert any(e.startswith(dotted(path)) for e in errors), errors
    assert not any(REJECTED[label] in e for e in errors)


@pytest.mark.parametrize("label", LABELS)
def test_variant_is_rejected_when_it_is_the_whole_field(label):
    errors, _ = check_incident_payload(put(full_payload(), FLAT, REJECTED[label]))
    assert errors


@pytest.mark.parametrize("label", LABELS)
def test_variant_is_rejected_in_by_without_echoing_it(label):
    errors = check_declared_name(REJECTED[label])
    assert errors and all(e.startswith("--by") for e in errors)
    assert not any(REJECTED[label] in e for e in errors)


@pytest.mark.parametrize("label", LABELS)
def test_variant_in_a_free_field_writes_nothing(world, label):
    rejection(lambda: world.open(title=f"Aviso {REJECTED[label]}"))
    assert world.written() == []


@pytest.mark.parametrize("label", LABELS)
def test_variant_in_a_nested_field_writes_nothing(world, label):
    rejection(lambda: world.open(analysis={"summary": f"Aviso {REJECTED[label]}"}))
    assert world.written() == []


@pytest.mark.parametrize("label", LABELS)
def test_variant_in_by_writes_nothing(world, label):
    rejection(
        lambda: record_incident(
            world.store, world.new_state(), kind=RecordKind.OPENED, by=REJECTED[label], now=RECORDED
        )
    )
    assert world.written() == []


@pytest.mark.parametrize("text", ACCEPTED)
@pytest.mark.parametrize("path", [FLAT, *NESTED], ids=dotted)
def test_ids_dates_and_references_are_not_false_positives(text, path):
    assert check_incident_payload(put(full_payload(), path, text)) == ([], [])


@pytest.mark.parametrize("text", ACCEPTED)
def test_ids_dates_and_references_are_accepted_in_by(text):
    assert check_declared_name(text) == []


def test_a_valid_ip_is_a_warning_even_with_many_digits():
    summary = ("state", "analysis", "summary")
    errors, warnings = check_incident_payload(
        put(full_payload(), summary, f"IP 203.0.113.7 y 2001:0db8::1 y {'1234:' * 7}5678")
    )
    assert errors == []
    assert len(warnings) == 1


def test_a_valid_dni_letter_is_required_when_it_is_separated():
    assert check_declared_name("12345678 Q") == []
    assert check_declared_name("12345678 Z")


def test_una_lista_de_ip_separadas_por_comas_es_un_aviso_no_un_telefono():
    summary = ("state", "analysis", "summary")
    errors, warnings = check_incident_payload(
        put(full_payload(), summary, "Origen 203.0.113.7,198.51.100.2")
    )

    assert errors == []
    assert any("dirección IP" in w for w in warnings)
