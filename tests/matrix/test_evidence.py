from datetime import timedelta

import pytest

from ens_evidence.evidence.layout import manifest_key
from ens_evidence.evidence.manifests import load_manifest_by_id, render_manifest
from ens_evidence.matrix.evidence import manual_support
from ens_evidence.models.readiness import BlockerKind
from ens_evidence.workspace import LocalStore

from .source_helpers import AT, ROW, collected_evidence, manual_evidence, manual_link

UNITS = frozenset({"org.1.1", "org.1.2"})
EVERY = 90


@pytest.fixture
def store(tmp_path):
    return LocalStore(tmp_path / "ws")


def support(store, evidence_id, *, every=EVERY, at=AT, covers=("base",)):
    return manual_support(store, ROW, manual_link(evidence_id, covers), at, every)


def only_blocker(result):
    assert not result.counts
    assert result.clauses == frozenset()
    return (result.blockers[0].kind, result.blockers[0].detail)


def test_una_evidencia_manual_integra_y_vigente_cuenta(store):
    evidence_id = manual_evidence(store)

    result = support(store, evidence_id)

    assert result.counts
    assert result.clauses == UNITS
    assert result.evidence[0].id == evidence_id
    assert result.reviewed_evidence[0].evidence_id == evidence_id
    assert result.available_at == load_manifest_by_id(store, evidence_id).provenance.submitted_at


def test_un_manifiesto_de_collector_no_cuenta_como_manual(store):
    evidence_id = collected_evidence(store)

    result = support(store, evidence_id)

    assert only_blocker(result) == (
        BlockerKind.MISSING_EVIDENCE,
        f"evidence_unverified:{evidence_id}",
    )


def test_un_manifiesto_manual_con_otra_fuente_no_cuenta(store):
    evidence_id = manual_evidence(store)
    manifest = load_manifest_by_id(store, evidence_id)
    store.write_bytes(
        manifest_key(evidence_id), render_manifest(manifest.model_copy(update={"source": "github"}))
    )

    assert only_blocker(support(store, evidence_id))[1] == f"evidence_unverified:{evidence_id}"


def test_artefacto_ausente(store):
    evidence_id = manual_evidence(store)
    (store._root / load_manifest_by_id(store, evidence_id).artifact.key).unlink()

    assert only_blocker(support(store, evidence_id)) == (
        BlockerKind.MISSING_EVIDENCE,
        f"evidence_unverified:{evidence_id}",
    )


def test_hash_distinto(store):
    evidence_id = manual_evidence(store)
    store.write_bytes(load_manifest_by_id(store, evidence_id).artifact.key, b"alterado")

    assert only_blocker(support(store, evidence_id))[1] == f"evidence_unverified:{evidence_id}"


def test_referencia_rota(store):
    assert only_blocker(support(store, "EV-20260101-deadbeef")) == (
        BlockerKind.MISSING_EVIDENCE,
        "evidence_unverified:EV-20260101-deadbeef",
    )


def test_sin_valid_until_cuenta_si_se_recogio_dentro_de_la_periodicidad(store):
    inside = manual_evidence(store, collected_at=AT - timedelta(days=EVERY), data=b"a")
    outside = manual_evidence(store, collected_at=AT - timedelta(days=EVERY, seconds=1), data=b"b")

    assert support(store, inside).counts
    assert only_blocker(support(store, outside)) == (
        BlockerKind.EXPIRED_EVIDENCE,
        f"evidence_expired:{outside}",
    )


def test_sin_valid_until_ni_periodicidad_no_cuenta(store):
    evidence_id = manual_evidence(store)

    assert only_blocker(support(store, evidence_id, every=None)) == (
        BlockerKind.MISSING_EVIDENCE,
        f"evidence_no_validity:{evidence_id}",
    )


def test_con_valid_until_manda_este_y_no_la_periodicidad(store):
    collected = AT - timedelta(days=200)
    live = manual_evidence(
        store, collected_at=collected, valid_until=AT + timedelta(days=1), data=b"a"
    )
    dead = manual_evidence(store, collected_at=collected, valid_until=AT, data=b"b")

    assert support(store, live, every=None).counts
    assert only_blocker(support(store, dead))[0] is BlockerKind.EXPIRED_EVIDENCE


def test_submitted_at_posterior_no_cuenta_aunque_collected_at_sea_anterior(store):
    evidence_id = manual_evidence(
        store, collected_at=AT - timedelta(days=10), now=AT + timedelta(hours=1)
    )

    assert only_blocker(support(store, evidence_id)) == (
        BlockerKind.MISSING_EVIDENCE,
        f"evidence_in_future:{evidence_id}",
    )
    assert support(store, evidence_id, at=AT + timedelta(hours=2)).counts


def test_covers_parcial_solo_sustenta_esas_clausulas(store):
    evidence_id = manual_evidence(store)

    assert support(store, evidence_id, covers=("org.1.1",)).clauses == frozenset({"org.1.1"})


def test_covers_ajeno_a_la_medida_bloquea(store):
    evidence_id = manual_evidence(store)

    result = support(store, evidence_id, covers=("R3",))

    assert not result.counts
    assert result.blockers[0].kind is BlockerKind.MISSING_EVIDENCE
