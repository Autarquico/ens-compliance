from datetime import timedelta

import pytest

from ens_evidence.evidence.layout import artifact_key, manifest_key, sha256_hex
from ens_evidence.evidence.manifests import render_manifest
from ens_evidence.models.contracts import (
    Artifact,
    EvidenceManifest,
    Provenance,
    ProvenanceKind,
)
from ens_evidence.models.enums import Role
from ens_evidence.scope import approve_scope

from .conftest import NOW, Session, snapshot_of, ui_client

VIEWS = ["/", "/matriz", "/evidencias", "/expediente", "/preauditoria"]
BODY_MARKER = "CUERPO-EN-BRUTO-NO-MOSTRAR"


def put_manifest(store, valid_for: timedelta | None) -> str:
    body = BODY_MARKER.encode()
    sha = sha256_hex(body)
    store.write_bytes(artifact_key(sha), body)
    manifest = EvidenceManifest(
        id="EV-20261101-abcdef12",
        source="manual",
        asset_id="lap-ana",
        collected_at=NOW - timedelta(days=7),
        valid_until=NOW + valid_for if valid_for else None,
        artifact=Artifact(storage="local", key=artifact_key(sha), sha256=sha),
        provenance=Provenance(
            kind=ProvenanceKind.MANUAL, submitted_by="Ana", submitted_at=NOW - timedelta(days=7)
        ),
    )
    store.write_bytes(manifest_key(manifest.id), render_manifest(manifest))
    return manifest.id


@pytest.mark.parametrize("path", VIEWS)
def test_views_render_on_an_empty_workspace(config, store, key, token, path):
    session = Session(ui_client(config, store, key), token)

    response = session.get(path)

    assert response.status_code == 200
    assert "Revisión humana" in response.text or path == "/evidencias"


@pytest.mark.parametrize("path", VIEWS)
def test_views_never_write(session, example_store, path):
    approve_scope(example_store, "Ana", Role.SYSTEM_OWNER, NOW)
    before = snapshot_of(example_store)

    assert session.get(path).status_code == 200

    assert snapshot_of(example_store) == before


def test_views_reject_writes(session, example_store):
    before = snapshot_of(example_store)

    for path in VIEWS:
        assert session.post(path, {}).status_code == 405

    assert snapshot_of(example_store) == before


def test_estado_points_to_the_wizard_until_scope_is_approved(session, example_store):
    page = session.get("/")
    assert 'href="/asistente/sistema"' in page.text
    assert "Sin aprobar" in page.text

    approve_scope(example_store, "Ana", Role.SYSTEM_OWNER, NOW)
    assert 'href="/asistente/informacion"' in session.get("/").text


def test_matriz_lists_official_measure_ids(session):
    page = session.get("/matriz")

    assert page.status_code == 200
    assert "<caption>" in page.text


def test_evidencias_shows_manifests_never_bodies(session, example_store):
    evidence_id = put_manifest(example_store, timedelta(days=30))

    page = session.get("/evidencias")

    assert evidence_id in page.text
    assert BODY_MARKER not in page.text
    assert sha256_hex(BODY_MARKER.encode()) not in page.text


def test_evidencias_marks_expired_and_current_manifests(session, example_store):
    put_manifest(example_store, timedelta(days=-1))
    assert "Caducado" in session.get("/evidencias").text

    put_manifest(example_store, timedelta(days=30))
    assert "Vigente" in session.get("/evidencias").text


def test_hostile_text_is_escaped(session, example_store):
    example_store.write_text(
        "scope/system.yaml",
        example_store.read_text("scope/system.yaml").replace(
            "Sistema de Taller Norte Software", "<script>alert(1)</script>"
        ),
    )

    page = session.get("/asistente/aprobacion-alcance")

    assert "<script>alert(1)</script>" not in page.text
    assert "&lt;script&gt;" in page.text


def test_estado_con_el_asistente_completo_habla_en_claro(completed):
    page = completed.get("/").text

    assert "applicability status" not in page
    assert "UNDETERMINED_APPLICABILITY" not in page
    assert "Falta firmar la Declaración de Aplicabilidad" in page
    assert "Siguiente paso" in page
    assert "Declaración de Aplicabilidad" in page


def test_matriz_vacia_explica_la_declaracion_cuando_el_asistente_esta_hecho(completed):
    assert (
        "se llena cuando se firma la Declaración de Aplicabilidad" in completed.get("/matriz").text
    )


def test_matriz_vacia_pide_el_asistente_si_falta(session):
    assert "Termina antes el alcance" in session.get("/matriz").text
