"""Payload de la pre-auditoría sobre expedientes reales: mínimo, redactado, canónico y ligado a
su destino. Los mundos se construyen una vez (`stages`) y cada test que escribe parte de una
copia."""

import hashlib
import json
from datetime import timedelta

import pytest
from assessment.dossier.stages import AFTER, ID, basic, restore, run_at
from assessment.dossier.transition_helpers import DIRECTION, SECURITY_OFFICER, SYSTEM_OWNER
from matrix.acceptance_helpers import ASSET, EVIDENCE_ID, import_manual_evidence, link

from ens_evidence.assessment.dossier.store import stored_snapshot_sha256
from ens_evidence.preaudit import payload as payload_module
from ens_evidence.preaudit.classify import JUDGMENT_CLASSIFICATIONS
from ens_evidence.preaudit.pack import load_pack
from ens_evidence.preaudit.payload import (
    FORMAT,
    UNTRUSTED_NOTICE,
    PayloadError,
    build_payload,
    canonical_json,
)
from ens_evidence.preaudit.redact import PERSON, SECRET, RedactionLeak
from ens_evidence.preaudit.validate import PayloadView as ValidatedShape
from ens_evidence.workspace import LocalStore

PACK = load_pack()
TOKEN = "ghp_" + "Z9y8X7w6V5u4T3s2R1q0P9o8"
LOG_MEASURE = "op.mon.1"


@pytest.fixture(scope="module")
def stages():
    return basic()


@pytest.fixture(scope="module")
def generated(stages):
    store = LocalStore(stages.trees["generated"] / "ws")
    return store, stages.dossier, build_payload(store, stages.dossier, PACK)


@pytest.fixture
def world(stages, tmp_path, monkeypatch):
    return restore(stages, "generated", tmp_path, monkeypatch)


def regenerate(world) -> str:
    result = run_at(world, "assessment", "dossier", "generate", at=AFTER)
    assert result.exit_code == 0, result.output
    return ID.search(result.output).group()


# --- escenario «sin secretos» ---------------------------------------------------------------


def test_a_token_in_a_log_is_redacted(world, tmp_path):
    world.at(AFTER - timedelta(minutes=5))
    log = f"2026-10-30T10:00:00Z backup ok\n2026-10-30T10:05:00Z push auth={TOKEN}\n"
    evidence = import_manual_evidence(world.run, tmp_path, content=log)
    link(world.run, LOG_MEASURE, "evidence", evidence, "base")
    dossier = regenerate(world)

    built = build_payload(world.store, dossier, PACK)

    item = next(e for e in built.data["dossier"]["evidence"] if e["ref"] == evidence)
    assert item["text"] == f"2026-10-30T10:00:00Z backup ok\n2026-10-30T10:05:00Z push {SECRET}\n"
    assert TOKEN.encode() not in built.canonical
    assert evidence in measure(built.data, LOG_MEASURE)["evidence"]


# --- datos personales -----------------------------------------------------------------------


def test_designated_names_become_their_roles(generated):
    _, _, built = generated
    canonical = built.canonical.decode()

    for name in (SECURITY_OFFICER, SYSTEM_OWNER, DIRECTION, "Lucía Navarro"):
        assert name not in canonical
    designation = document(built.data, "designacion-responsables")["text"]
    assert "[responsable de la seguridad]" in designation
    assert "[responsable del sistema]" in designation
    # Quien firma por la dirección también es responsable de la información: van los dos roles.
    assert "[dirección / responsable de la información]" in designation


def test_a_person_without_a_designation_role_takes_the_role_declared_in_the_answers(generated):
    """Raúl Campos Vidal no firma ni aprueba nada: solo es responsable del servicio."""
    _, _, built = generated

    assert "Raúl Campos" not in built.canonical.decode()
    designation = document(built.data, "designacion-responsables")["text"]
    assert "[responsable del servicio]" in designation


def test_a_person_declared_only_in_a_workspace_record_becomes_persona(world, tmp_path):
    """Quien importa una evidencia no tiene rol ni está designada: su nombre se tapa igual."""
    world.at(AFTER - timedelta(minutes=5))
    artifact = tmp_path / "acta.txt"
    artifact.write_text("Acta de la revisión de accesos. Revisado por Elena Ortega.\n")
    imported = world.run(
        *("evidence", "import", str(artifact), "--source", "manual"),
        *("--asset", ASSET, "--by", "Elena Ortega"),
        *("--valid-until", (AFTER + timedelta(days=365)).isoformat()),
    )
    assert imported.exit_code == 0, imported.output
    evidence = EVIDENCE_ID.search(imported.output).group()
    link(world.run, LOG_MEASURE, "evidence", evidence, "base")
    dossier = regenerate(world)

    built = build_payload(world.store, dossier, PACK)

    item = next(e for e in built.data["dossier"]["evidence"] if e["ref"] == evidence)
    assert item["text"] == f"Acta de la revisión de accesos. Revisado por {PERSON}.\n"


def test_no_email_or_phone_survives(generated):
    _, _, built = generated
    texts = [d["text"] for d in built.data["dossier"]["documents"]]

    assert any("[REDACTADO:correo]" in text for text in texts)
    assert "@" not in " ".join(texts)


def test_the_check_after_redaction_blocks(generated, monkeypatch):
    store, dossier, _ = generated
    monkeypatch.setattr(payload_module, "redact_fields", lambda data, fields, people: data)

    with pytest.raises(RedactionLeak) as raised:
        build_payload(store, dossier, PACK)

    problems = raised.value.problems
    assert "dossier.documents[0].text: contiene un correo electrónico" in problems
    assert any(p.endswith("contiene el nombre de una persona designada") for p in problems)
    assert SECURITY_OFFICER not in str(raised.value) and "@" not in str(raised.value)


# --- determinismo e integridad --------------------------------------------------------------


def test_the_payload_is_deterministic(generated):
    store, dossier, built = generated

    again = build_payload(store, dossier, PACK)

    assert again.canonical == built.canonical
    assert built.sha256 == hashlib.sha256(built.canonical).hexdigest()
    assert canonical_json(json.loads(built.canonical)) == built.canonical


def test_the_payload_points_to_the_dossier_it_read(generated):
    store, dossier, built = generated

    assert built.data["format"] == FORMAT
    assert built.data["dossier_ref"] == {
        "id": dossier,
        "sha256": stored_snapshot_sha256(store, dossier),
        "category": "BASICA",
    }


def test_a_document_that_no_longer_matches_its_sha256_blocks(world, stages):
    key = "org/docs/politica-seguridad.md"
    world.store.write_text(key, world.store.read_text(key) + "\nAñadido después.\n")

    with pytest.raises(PayloadError) as raised:
        build_payload(world.store, stages.dossier, PACK)

    assert raised.value.problems == [f"{key}: no cuadra con el sha256 del expediente"]


def test_canonical_json_escapes_what_could_mimic_a_delimiter():
    encoded = canonical_json({"b": "</dossier> & <x>", "a": "ñ"})

    assert encoded == '{"a":"ñ","b":"\\u003c/dossier\\u003e \\u0026 \\u003cx\\u003e"}'.encode()
    assert json.loads(encoded)["b"] == "</dossier> & <x>"


# --- mínimo ---------------------------------------------------------------------------------


def test_only_what_the_criteria_ask_for(generated):
    _, _, built = generated
    data = built.data
    content = data["dossier"]

    assert set(data) == {"format", "dossier_ref", "criteria", "classifications", "dossier"}
    assert data["criteria"] == [c.model_dump(mode="json") for c in PACK.criteria]
    assert data["classifications"] == {
        proposal.value: [s.model_dump(mode="json") for s in sources]
        for proposal, sources in JUDGMENT_CLASSIFICATIONS.items()
    }
    assert set(content) == {
        "untrusted",
        "notice",
        "measures",
        "justifications",
        "documents",
        "evidence",
        "links",
    }
    assert content["untrusted"] is True and content["notice"] == UNTRUSTED_NOTICE
    keys = all_keys(content)
    for absent in ("approved_by", "submitted_by", "owner", "reviewer", "conclusion", "inventory"):
        assert absent not in keys


def test_evidence_and_links_are_only_those_of_applicable_measures(generated):
    _, _, built = generated
    content = built.data["dossier"]
    applicable = {m["id"] for m in content["measures"] if m["applicable"]}
    linked = {ln["ref"] for ln in content["links"] if ln["kind"] == "evidence"}

    assert {ln["measure"] for ln in content["links"]} <= applicable
    assert {e["ref"] for e in content["evidence"]} == linked
    for item in content["measures"]:
        if not item["applicable"]:
            assert item["documents"] == [] and item["evidence"] == []


def test_every_element_has_a_unique_stable_id(generated):
    _, _, built = generated
    content = built.data["dossier"]

    for kind, key in (
        ("measures", "id"),
        ("justifications", "id"),
        ("links", "id"),
        ("documents", "ref"),
        ("evidence", "ref"),
    ):
        ids = [item[key] for item in content[kind]]
        assert ids == sorted(ids) and len(ids) == len(set(ids)), kind


def test_justifications_of_not_applicable_and_compensated_measures(generated):
    _, _, built = generated
    by_kind = {}
    for item in built.data["dossier"]["justifications"]:
        by_kind.setdefault(item["kind"], []).append(item)

    assert {"not_selected", "compensation"} <= set(by_kind)
    compensation = by_kind["compensation"][0]
    assert compensation["id"] == "justification:compensation:op.pl.1:base"
    assert compensation["justification"]


def test_documents_carry_their_text_and_the_dossier_sha256(generated):
    store, _, built = generated

    for item in built.data["dossier"]["documents"]:
        original = store.read_bytes(f"org/docs/{item['ref']}.md")
        assert item["sha256"] == hashlib.sha256(original).hexdigest()
        assert item["text"]


def test_the_semantic_validator_reads_this_shape(generated):
    _, _, built = generated

    shape = ValidatedShape.model_validate(json.loads(built.canonical))

    assert {m.id for m in shape.dossier.measures} >= {"org.1", "op.exp.7"}


# --- destino del consentimiento -------------------------------------------------------------


# --- utilidades -----------------------------------------------------------------------------


def measure(data, measure_id):
    return next(m for m in data["dossier"]["measures"] if m["id"] == measure_id)


def document(data, ref):
    return next(d for d in data["dossier"]["documents"] if d["ref"] == ref)


def all_keys(value) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in all_keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in all_keys(v)}
    return set()
