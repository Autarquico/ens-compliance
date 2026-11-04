"""Redacción del payload: lo que detectan `find_secrets` y `privacy_text` se sustituye, y lo que
quede detectable bloquea el envío nombrando la ruta, nunca el valor."""

import base64
from urllib.parse import quote

import pytest

from ens_evidence.models.enums import Role
from ens_evidence.preaudit.redact import (
    IP,
    PERSON,
    SECRET,
    RedactionLeak,
    leaks,
    redact_fields,
    redact_text,
    require_clean,
)
from ens_evidence.privacy_text import text_problems
from ens_evidence.scope.secrets import find_secrets

TOKEN = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2"
OFFICER = "Lucía Navarro Gil"
DESIGNATED = {OFFICER: [Role.SECURITY_OFFICER], "Hugo Serrano Ortiz": [Role.SYSTEM_OWNER]}
LOG = f"""2026-10-30T10:00:00Z deploy ok auth={TOKEN} desde 203.0.113.7
2026-10-30T10:01:00Z password: hunter2
2026-10-30T10:02:00Z url https://deploy:s3cr3t@git.example.com/infra.git
"""


def clean(text: str) -> bool:
    return not find_secrets(text) and text_problems(text) == ([], [])


def test_token_in_a_log_is_redacted():
    redacted = redact_text(LOG, {})

    assert TOKEN not in redacted and "hunter2" not in redacted and "s3cr3t" not in redacted
    assert redacted.count(SECRET) == 3
    assert clean(redacted)


def test_timestamps_and_prose_around_a_secret_survive():
    redacted = redact_text(LOG, {})

    assert redacted.splitlines()[0] == f"2026-10-30T10:00:00Z deploy ok {SECRET} desde {IP}"


def test_private_key_block_is_redacted_to_its_end():
    text = "\n".join(
        ["antes", "-----BEGIN RSA PRIVATE KEY-----", "MIIEowIBAAKCAQEA", "QUJD"]
        + ["-----END RSA PRIVATE KEY-----", "después"]
    )

    assert redact_text(text, {}).splitlines() == [
        "antes",
        SECRET,
        SECRET,
        SECRET,
        SECRET,
        "después",
    ]


@pytest.mark.parametrize(
    ("text", "mark"),
    [
        ("Escribe a ana.perez@example.com hoy", "[REDACTADO:correo]"),
        ("Llama al 612 345 678 hoy", "[REDACTADO:telefono]"),
        ("Titular 12345678Z hoy", "[REDACTADO:dni-nie]"),
        ("NIE X1234567L hoy", "[REDACTADO:dni-nie]"),
        ("Cuenta ES91 2100 0418 4502 0005 1332 hoy", "[REDACTADO:iban]"),
        ("Servidor 2001:db8::1 hoy", IP),
        ("Servidor 192.0.2.10 hoy", IP),
    ],
)
def test_personal_data_becomes_its_type(text, mark):
    redacted = redact_text(text, {})

    assert redacted.endswith(" hoy") and mark in redacted
    assert clean(redacted)


def test_designated_names_become_their_role():
    text = "Firma LUCÍA NAVARRO  GIL. Avisa a Lucía Navarro o a Hugo Serrano Ortiz; no a Lucía."

    assert redact_text(text, DESIGNATED) == (
        "Firma [responsable de la seguridad]. Avisa a [responsable de la seguridad] o a "
        "[responsable del sistema]; no a Lucía."
    )


def test_a_person_with_two_roles_gets_both():
    designated = {OFFICER: [Role.SECURITY_OFFICER], "lucía navarro gil": [Role.SYSTEM_OWNER]}

    assert redact_text(OFFICER, designated) == (
        "[responsable de la seguridad / responsable del sistema]"
    )


def test_prose_without_sensitive_data_is_untouched():
    text = "La política se revisa cada 365 días según el RD 311/2022, anexo II, org.1."

    assert redact_text(text, DESIGNATED) == text


def test_only_the_named_fields_are_redacted():
    data = {"id": "EV-20261030-4f1c4929", "items": [{"text": LOG, "sha256": "1" * 64}]}

    redacted = redact_fields(data, {"text"}, {})

    assert redacted["id"] == data["id"] and redacted["items"][0]["sha256"] == "1" * 64
    assert TOKEN not in redacted["items"][0]["text"]


def test_leftovers_block_naming_the_path_never_the_value():
    data = {"dossier": {"documents": [{"text": f"Contacto: ana@example.com {OFFICER}"}]}}

    with pytest.raises(RedactionLeak) as raised:
        require_clean(data, {"text"}, DESIGNATED)

    assert raised.value.problems == [
        "dossier.documents[0].text: contiene un correo electrónico",
        "dossier.documents[0].text: contiene el nombre de una persona designada",
    ]
    assert "ana@example.com" not in str(raised.value) and OFFICER not in str(raised.value)


def test_a_secret_anywhere_blocks_even_outside_the_text_fields():
    problems = leaks({"meta": {"note": TOKEN}}, {"text"}, {})

    assert problems == ["meta.note: parece un secreto"]
    assert TOKEN not in problems[0]


def test_an_ip_left_behind_blocks():
    assert leaks({"text": "desde 203.0.113.7"}, {"text"}, {}) == [
        "text: contiene una dirección IP, que puede ser un dato personal"
    ]


def test_redacted_output_passes_the_check():
    data = {"text": LOG + f"Contacto: ana@example.com, {OFFICER}, 612 345 678"}

    require_clean(redact_fields(data, {"text"}, DESIGNATED), {"text"}, DESIGNATED)


def test_a_declared_person_without_a_role_becomes_persona():
    people = {**DESIGNATED, "Elena Ortega": []}

    redacted = redact_text("Revisado por Elena Ortega y por Lucía Navarro.", people)

    assert redacted == f"Revisado por {PERSON} y por [responsable de la seguridad]."


# --- formatos de secreto que no son el habitual ------------------------------------------------

LONG_TOKEN = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"


@pytest.mark.parametrize(
    "secret",
    [
        "glpat-" + "Ab12Cd34Ef56Gh78Ij90",
        "sk_live_" + "51HxAbCdEfGh1234IjKl",
        "rk_test_" + "51HxAbCdEfGh1234IjKl",
        "AIza" + "SyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q",
        "https://hooks.slack.com/services/T0123ABCD/B0456EFGH/" + "abcdEFGH1234ijklMNOP5678",
        "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        base64.b64encode(LONG_TOKEN.encode()).decode(),
        base64.urlsafe_b64encode(b"password=hunter2-largo").decode().rstrip("="),
        quote(LONG_TOKEN, safe=""),
        "Xk9Qm2Vr7Lp4Nz8Bt5Wc1Hy6Jd3Fg0Sa",
    ],
)
def test_secrets_in_other_formats_are_redacted_and_never_reach_the_second_pass(secret):
    text = f"antes {secret} después"

    redacted = redact_text(text, {})

    assert redacted == f"antes {SECRET} después"
    assert leaks({"text": text}, {"text"}, {}) != []
    assert leaks({"text": redacted}, {"text"}, {}) == []


def test_a_pgp_private_key_block_is_redacted_to_its_end():
    text = "antes\n-----BEGIN PGP PRIVATE KEY BLOCK-----\nlQOYBF0abc\n=XyZ1\n"
    text += "-----END PGP PRIVATE KEY BLOCK-----\ndespués"

    assert redact_text(text, {}).splitlines() == ["antes", *[SECRET] * 4, "después"]


def test_a_token_split_across_two_lines_takes_both():
    text = f"auth {LONG_TOKEN[:14]}\n{LONG_TOKEN[14:]} fin\nsiguiente"

    assert redact_text(text, {}).splitlines() == [SECRET, SECRET, "siguiente"]
    assert leaks({"text": text}, {"text"}, {}) != []


@pytest.mark.parametrize(
    "line",
    [
        "Authorization: Bearer abcDEF1234567890ghiJKL",
        "password: correct horse battery staple",
    ],
)
def test_a_labelled_secret_is_redacted_whole(line):
    redacted = redact_text(f"{line}\nsiguiente", {})

    assert redacted == f"{SECRET}\nsiguiente"


@pytest.mark.parametrize(
    "text",
    [
        "ruta /usr/local/lib/python3.12/site-packages/SomeThing",
        "id ThisIsAVeryLongIdentifierName123",
        "Configuration-Management-Policy-Version2",
        "sha256 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
        "El servicio usa Bearer authentication y tokens rotados.",
    ],
)
def test_paths_identifiers_digests_and_prose_are_not_secrets(text):
    assert redact_text(text, {}) == text
    assert leaks({"text": text}, {"text"}, {}) == []


# --- variantes de los nombres designados -------------------------------------------------------


@pytest.mark.parametrize(
    "cited",
    [
        "Lucia Navarro Gil",
        "Navarro Gil, Lucía",
        "navarro gil,lucia",
        "Lu​cía Navarro Gil",
        "LUCÍA NAVARRO",
        "Lucía Navarro Gil",
    ],
)
def test_variants_of_a_designated_name_become_the_role(cited):
    text = f"Firma {cited} hoy."

    assert redact_text(text, DESIGNATED) == "Firma [responsable de la seguridad] hoy."
    assert leaks({"text": text}, {"text"}, DESIGNATED) == [
        "text: contiene el nombre de una persona designada"
    ]


def test_the_first_name_alone_is_not_redacted():
    text = "Lucía revisó el registro."

    assert redact_text(text, DESIGNATED) == text
    assert leaks({"text": text}, {"text"}, DESIGNATED) == []
