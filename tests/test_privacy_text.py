import pytest
from incidents.variants import ACCEPTED, REJECTED

from ens_evidence.privacy_text import text_problems


@pytest.mark.parametrize("label", list(REJECTED))
def test_variant_is_an_error_without_echoing_the_value(label):
    errors, _ = text_problems(f"Aviso {REJECTED[label]} fin")
    assert errors
    assert not any(REJECTED[label] in e for e in errors)


@pytest.mark.parametrize("label", list(REJECTED))
def test_variant_is_an_error_as_the_whole_text(label):
    assert text_problems(REJECTED[label])[0]


@pytest.mark.parametrize("text", ACCEPTED)
def test_ids_dates_and_references_are_clean(text):
    assert text_problems(text) == ([], [])


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("a@example.org", "un correo electrónico"),
        ("12345678Z", "un DNI/NIE"),
        ("ES9121000418450200051332", "un IBAN"),
        ("612345678", "un teléfono"),
        ("password=abc123", "una credencial"),
    ],
)
def test_errors_name_the_kind(text, kind):
    assert f"contiene {kind}" in text_problems(text)[0]


def test_a_valid_ip_is_a_warning_not_an_error():
    errors, warnings = text_problems(f"IP 203.0.113.7 y 2001:0db8::1 y {'1234:' * 7}5678")
    assert errors == []
    assert len(warnings) == 1
    assert "203.0.113.7" not in warnings[0]


@pytest.mark.parametrize(
    "text", ["203.0.113.7:8080", "203.0.113.7,198.51.100.2", "2026-10-15T12:30:00+02:00"]
)
def test_ip_with_port_ip_list_and_iso_instants_are_not_phones(text):
    assert text_problems(text)[0] == []
