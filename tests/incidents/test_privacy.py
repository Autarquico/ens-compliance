import copy

import pytest

from ens_evidence.incidents import check_incident_payload

from .helpers import full_payload

# Datos sintéticos (el IBAN es el de ejemplo público de la documentación bancaria).
SENSITIVE = {
    "correo": "contacto.ficticio@example.org",
    "dni": "12345678Z",
    "nie": "X1234567L",
    "iban": "ES91 2100 0418 4502 0005 1332",
    "teléfono": "612 345 678",
    "teléfono con prefijo": "+34 612345678",
    "secreto": "AKIAABCDEFGHIJKLMNOP",
}

# Campo plano y campos anidados de texto libre.
FLAT = ("state", "title")
NESTED = [
    ("state", "analysis", "summary"),
    ("state", "analysis", "recommendations", 0),
    ("state", "actions", 0, "description"),
    ("state", "source", "system"),
    ("state", "procedure", "name"),
    ("state", "closure", "conclusion"),
    ("state", "closure", "legal_advice"),
    ("state", "closure", "auditable_events_review", "detail"),
]


def put(payload, path, value):
    target = payload
    for step in path[:-1]:
        target = target[step]
    target[path[-1]] = value
    return payload


def dotted(path):
    out = ""
    for step in path:
        out += f"[{step}]" if isinstance(step, int) else (f".{step}" if out else step)
    return out


def test_full_payload_has_no_errors_nor_warnings():
    assert check_incident_payload(full_payload()) == ([], [])


def test_minimal_payload_is_clean():
    payload = {k: v for k, v in full_payload().items() if k != "state"}
    payload["state"] = {"id": "INC-2026-001", "title": "x", "closure": None, "source": None}
    assert check_incident_payload(payload) == ([], [])


@pytest.mark.parametrize("label", list(SENSITIVE))
@pytest.mark.parametrize("path", [FLAT, *NESTED], ids=dotted)
def test_sensitive_value_is_rejected_naming_the_field_but_not_the_value(label, path):
    value = f"Aviso: {SENSITIVE[label]} fue visto"
    errors, _ = check_incident_payload(put(full_payload(), path, value))
    assert any(e.startswith(dotted(path)) for e in errors), errors
    assert not any(raw in e for e in errors for raw in SENSITIVE.values())


def test_error_does_not_contain_the_value_even_when_it_is_the_whole_field():
    payload = put(full_payload(), FLAT, SENSITIVE["correo"])
    errors, _ = check_incident_payload(payload)
    assert errors and all("example.org" not in e and "contacto" not in e for e in errors)


def test_secret_value_in_a_structured_field_is_rejected():
    payload = put(full_payload(), ("state", "procedure", "approval_key"), SENSITIVE["secreto"])
    errors, _ = check_incident_payload(payload)
    assert [e for e in errors if e.startswith("state.procedure.approval_key")]


def test_secret_in_alert_ids_is_rejected_by_format():
    payload = put(full_payload(), ("state", "source", "alert_ids"), ["ok-1", "sk-" + "a" * 30])
    errors, _ = check_incident_payload(payload)
    assert any(e.startswith("state.source.alert_ids[1]") for e in errors)
    assert not any("sk-" in e for e in errors)


def test_ip_in_free_text_is_a_warning_not_an_error():
    payload = put(
        full_payload(), ("state", "analysis", "summary"), "Origen 203.0.113.7 y 2001:db8::1"
    )
    errors, warnings = check_incident_payload(payload)
    assert errors == []
    assert len(warnings) == 1
    assert warnings[0].startswith("state.analysis.summary")
    assert "203.0.113.7" not in warnings[0]


@pytest.mark.parametrize(
    "text", ["Versión 1.2.3.4.5", "Hora 12:30:45", "Fecha 2026-10-15 y 20261015 a las", "999.1.1.1"]
)
def test_non_identifiers_do_not_trigger(text):
    payload = put(full_payload(), ("state", "analysis", "summary"), text)
    assert check_incident_payload(payload) == ([], [])


def test_iban_needs_valid_checksum():
    payload = put(full_payload(), FLAT, "Ref ES00 2100 0418 4502 0005 1332 interna")
    assert check_incident_payload(payload) == ([], [])


@pytest.mark.parametrize("alert", ["has space", "a@b.es", "x" * 129, "", 7])
def test_alert_id_format(alert):
    payload = put(full_payload(), ("state", "source", "alert_ids"), [alert])
    errors, _ = check_incident_payload(payload)
    assert any(e.startswith("state.source.alert_ids[0]") for e in errors)


def test_assets_must_be_opaque_ids():
    payload = put(full_payload(), ("state", "assets"), ["APP-001", "Portátil de Ana"])
    errors, _ = check_incident_payload(payload)
    assert any(e.startswith("state.assets[1]") for e in errors)


def test_unknown_top_level_and_nested_fields_are_rejected():
    payload = full_payload()
    payload["attachment"] = "log.txt"
    payload["state"]["analysis"]["raw_log"] = "x"
    payload["state"]["actions"][0]["screenshot"] = "x"
    errors, _ = check_incident_payload(payload)
    assert sorted(errors) == [
        "attachment: campo no admitido",
        "state.actions[0].screenshot: campo no admitido",
        "state.analysis.raw_log: campo no admitido",
    ]


def test_unknown_field_whose_name_is_a_value_is_not_echoed():
    payload = full_payload()
    payload["state"]["analysis"][SENSITIVE["correo"]] = "x"
    errors, _ = check_incident_payload(payload)
    assert errors == ["state.analysis.<clave>: campo no admitido"]


@pytest.mark.parametrize(
    "path", [("state", "analysis"), ("state", "actions"), ("state", "title"), ("state", "source")]
)
def test_wrong_structure_is_rejected(path):
    swapped = {("state", "title"): {"a": 1}, ("state", "actions"): "x"}.get(path, [1])
    errors, _ = check_incident_payload(put(full_payload(), path, swapped))
    assert errors


def test_secret_key_names_are_not_hidden_in_unknown_fields():
    payload = full_payload()
    payload["state"]["password"] = "hunter2hunter2"
    errors, _ = check_incident_payload(payload)
    assert errors == ["state.password: campo no admitido"]


def test_payload_is_not_mutated():
    payload = put(full_payload(), FLAT, SENSITIVE["correo"])
    before = copy.deepcopy(payload)
    check_incident_payload(payload)
    assert payload == before


OPAQUE_LISTS = [("state", "assets"), ("state", "source", "alert_ids")]
PERSONAL_IDS = {
    "dni": "12345678Z",
    "nie": "X1234567L",
    "iban": "ES9121000418450200051332",
    "teléfono": "600123456",
    "correo": "ana@example.org",
}


@pytest.mark.parametrize("path", OPAQUE_LISTS, ids=lambda p: p[-1])
@pytest.mark.parametrize("kind", PERSONAL_IDS)
def test_personal_identifiers_are_rejected_in_opaque_id_lists(path, kind):
    payload = put(full_payload(), (*path, 0), PERSONAL_IDS[kind])

    errors, _ = check_incident_payload(payload)

    assert any(e.startswith(f"{'.'.join(path)}[0]:") for e in errors)
    assert PERSONAL_IDS[kind] not in " ".join(errors)


@pytest.mark.parametrize("path", OPAQUE_LISTS, ids=lambda p: p[-1])
def test_an_ip_in_an_opaque_id_list_is_a_warning(path):
    payload = put(full_payload(), (*path, 0), "192.168.1.10")

    errors, warnings = check_incident_payload(payload)

    assert errors == []
    assert any("dirección IP" in w for w in warnings)


def test_ordinary_asset_and_alert_ids_still_pass():
    payload = put(full_payload(), ("state", "assets", 0), "srv-ficheros-01")
    payload = put(payload, ("state", "source", "alert_ids", 0), "1700000000.1234")

    assert check_incident_payload(payload) == ([], [])
