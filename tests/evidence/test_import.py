from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from ens_evidence.evidence import (
    EvidenceImportError,
    import_manual,
    list_manifests,
    load_manifest_by_id,
    verify_all,
)
from ens_evidence.evidence.layout import artifact_key, sha256_hex
from ens_evidence.models import EvidenceManifest, Provenance, ProvenanceKind
from ens_evidence.models.contracts import Artifact

from .helpers import NOW

DATA = b"contenido de la evidencia"


def importar(store, data=DATA, **overrides):
    args = {"asset_id": "asset-1", "submitted_by": "ana", "now": NOW, **overrides}
    return import_manual(store, data, **args)


def test_importar_guarda_artefacto_por_hash_y_manifiesto_manual_sin_collector(store):
    manifest = importar(store)

    assert store.read_bytes(artifact_key(sha256_hex(DATA))) == DATA
    assert manifest.artifact.sha256 == sha256_hex(DATA)
    assert manifest.provenance.kind is ProvenanceKind.MANUAL
    assert manifest.provenance.submitted_by == "ana"
    assert manifest.collector is None
    assert manifest.collected_at == NOW
    assert load_manifest_by_id(store, manifest.id) == manifest


def test_importar_un_binario_conserva_los_bytes_exactos(store):
    binary = bytes(range(256)) + b"\xff\xfe\x00"

    manifest = importar(store, binary)

    assert store.read_bytes(manifest.artifact.key) == binary
    assert verify_all(store).ok


def test_el_mismo_contenido_dos_veces_reutiliza_el_artefacto_con_manifiestos_distintos(store):
    first = importar(store)
    second = importar(store)

    assert first.id != second.id
    assert first.artifact.key == second.artifact.key
    assert len(store.list("evidence/raw")) == 1
    assert len(list_manifests(store).manifests) == 2


def test_artefacto_existente_con_contenido_distinto_aborta_sin_crear_manifiesto(store):
    store.create_bytes(artifact_key(sha256_hex(DATA)), b"otra cosa")

    with pytest.raises(EvidenceImportError, match="no coincide con su hash"):
        importar(store)

    assert store.list("evidence/manifests") == []


def test_si_el_id_choca_con_un_manifiesto_existente_reintenta_con_otro(store, monkeypatch):
    ids = iter(["EV-20260601-aaaaaaaa", "EV-20260601-aaaaaaaa", "EV-20260601-bbbbbbbb"])
    monkeypatch.setattr("ens_evidence.evidence.importer.new_evidence_id", lambda now: next(ids))
    first = importar(store, b"uno")

    second = importar(store, b"dos")

    assert (first.id, second.id) == ("EV-20260601-aaaaaaaa", "EV-20260601-bbbbbbbb")
    assert load_manifest_by_id(store, first.id).artifact.sha256 == sha256_hex(b"uno")


def test_si_todos_los_ids_chocan_falla_e_informa_del_huerfano(store, monkeypatch):
    monkeypatch.setattr(
        "ens_evidence.evidence.importer.new_evidence_id", lambda now: "EV-20260601-aaaaaaaa"
    )
    importar(store, b"uno")

    with pytest.raises(EvidenceImportError, match="huérfano"):
        importar(store, b"dos")

    assert len(list_manifests(store).manifests) == 1
    assert not verify_all(store).ok


def test_valid_until_debe_ser_posterior_a_collected_at(store):
    for valid_until in (NOW, NOW - timedelta(days=1)):
        with pytest.raises(ValidationError, match="valid_until"):
            importar(store, valid_until=valid_until)

    assert store.list("evidence") == []


def test_valid_until_posterior_a_collected_at_se_acepta(store):
    manifest = importar(store, valid_until=NOW + timedelta(seconds=1))

    assert manifest.valid_until == NOW + timedelta(seconds=1)


def test_collected_at_indicado_por_la_persona_prevalece_sobre_el_momento_de_importacion(store):
    collected = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)

    assert importar(store, collected_at=collected).collected_at == collected


def make_manifest(**overrides):
    base = {
        "id": "EV-20260601-aaaaaaaa",
        "source": "github",
        "asset_id": "a",
        "collected_at": NOW,
        "artifact": Artifact(storage="workspace", key="evidence/raw/" + "a" * 64, sha256="a" * 64),
        "provenance": Provenance(kind=ProvenanceKind.COLLECTOR, submitted_by="x", submitted_at=NOW),
        "collector": {"name": "fake", "version": "1"},
    }
    return EvidenceManifest(**{**base, **overrides})


def test_collector_es_obligatorio_cuando_la_procedencia_es_collector():
    assert make_manifest().collector is not None
    with pytest.raises(ValidationError, match="collector"):
        make_manifest(collector=None)


def test_manifiesto_manual_acepta_collector_opcional():
    provenance = Provenance(kind=ProvenanceKind.MANUAL, submitted_by="ana", submitted_at=NOW)

    assert make_manifest(provenance=provenance, collector=None).collector is None
    assert make_manifest(provenance=provenance).collector is not None


def test_manifiesto_sin_provenance_se_rechaza():
    with pytest.raises(ValidationError, match="provenance"):
        EvidenceManifest(
            id="EV-20260601-aaaaaaaa",
            source="s",
            asset_id="a",
            collected_at=NOW,
            artifact={"storage": "workspace", "key": "k", "sha256": "a" * 64},
        )
