"""Proyección de la última recogida de cada conector (ui-conectores, T4 + D3, D4, D8, D12)."""

import hashlib
import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta

import pytest

from ens_evidence.evidence.importer import import_collected
from ens_evidence.evidence.layout import artifact_key, manifest_key
from ens_evidence.evidence.manifests import render_manifest
from ens_evidence.models.contracts import (
    Artifact,
    CollectionResult,
    CollectorInfo,
    EvidenceManifest,
    Provenance,
    ProvenanceKind,
)
from ens_evidence.ui.store import UiStore
from ens_evidence.ui.views import conectores_recogida as rec
from ens_evidence.workspace import LocalStore

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
HOSTILE = "<script>alert(1)</script> ghp_abcdefghijklmnopqrstuvwxyz0123456789"


@pytest.fixture
def store(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    return UiStore(LocalStore(root))


def result(name, status="ERROR", data=None, asset="asset-1", at=NOW):
    return CollectionResult(
        source=name,
        collector=name,
        collected_at=at,
        asset_id=asset,
        data=data or {},
        collection_status=status,
    )


def imported(store, res):
    return import_collected(store, res, collector_version="1", now=res.collected_at)


def last(store, name):
    manifest = rec.index_collections(store).latest[name]
    return rec.last_collection(store, manifest, name)


def strings(dto):
    out = []

    def walk(value):
        if isinstance(value, dict):
            for k, v in value.items():
                walk(k)
                walk(v)
        elif isinstance(value, list | tuple):
            for v in value:
                walk(v)
        else:
            out.append(str(value))

    walk(asdict(dto))
    return " | ".join(out)


def put_raw(store, raw: bytes, *, name="github", key=None, asset="asset-1", kind="collector"):
    """Un manifiesto cuyo artefacto es `raw` tal cual (con el hash correcto salvo que se diga)."""
    sha = hashlib.sha256(raw).hexdigest()
    key = key or artifact_key(sha)
    store._inner.write_bytes(key, raw)
    manifest = EvidenceManifest(
        id=f"EV-20261117-{sha[:8]}",
        source=name,
        asset_id=asset,
        collected_at=NOW,
        artifact=Artifact(storage="workspace", key=key, sha256=sha),
        provenance=Provenance(
            kind=ProvenanceKind(kind), submitted_by="collector:x", submitted_at=NOW
        ),
        collector=CollectorInfo(name=name, version="1"),
    )
    store._inner.write_text(manifest_key(manifest.id), render_manifest(manifest).decode())
    return manifest


def document(name="github", status="SUCCESS", data=None, asset="asset-1"):
    return json.dumps(
        {
            "schema_version": "1.0",
            "source": name,
            "collector": name,
            "collected_at": NOW.isoformat(),
            "asset_id": asset,
            "data": data or {},
            "collection_status": status,
        }
    ).encode()


# --- categorías por forma de conector ------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "error", "category"),
    [
        ("github", "ENS_GITHUB_TOKEN no está definida", "credential_missing"),
        ("cloudflare", "ENS_CLOUDFLARE_API_TOKEN no está definida", "credential_missing"),
        ("github", "HTTP 401: credencial rechazada", "credential_rejected"),
        ("cloudflare", "el token no está activo", "credential_rejected"),
        (
            "google-workspace",
            "la clave de la cuenta de servicio no es válida",
            "credential_rejected",
        ),
        ("github", "ENS_GITHUB_TOKEN contiene caracteres no válidos", "credential_rejected"),
        ("restic", "falta la variable de entorno ENS_BACKUP_RESTIC_PASSWORD", "credential_missing"),
        ("restic", "contraseña del repositorio incorrecta (código 12)", "credential_rejected"),
        ("github", "HTTP 403: permiso insuficiente", "permission_denied"),
        ("github", "HTTP 404: no encontrado o sin acceso", "permission_denied"),
        ("github", "algo inesperado con " + HOSTILE, "connector_error"),
    ],
)
def test_known_error_literals_map_to_fixed_categories(store, name, error, category):
    imported(store, result(name, data={"error": error}))
    found = last(store, name)
    assert found.failure.category == category
    assert found.failure.label == rec.CATEGORY_LABELS[category]
    assert error not in strings(found)
    assert not found.partial


def test_restic_missing_env_var_name_is_not_echoed(store):
    error = "falta la variable de entorno ENS_BACKUP_RESTIC_PASSWORD"
    imported(store, result("restic", data={"error": error}))
    found = last(store, "restic")
    assert found.failure.category == "credential_missing"
    assert "ENS_BACKUP" not in strings(found)


def test_keystore_sections_only_with_missing_token(store):
    sections = {
        "keys": {"status": "ERROR", "reason": "ENS_KEYSTORE_TOKEN no está definida"},
        "tokens": {"status": "ERROR", "reason": "ENS_KEYSTORE_TOKEN no está definida"},
    }
    imported(store, result("keystore", data={"sections": sections}))
    found = last(store, "keystore")
    assert found.failure.category == "credential_missing"
    assert [(s.name, s.status) for s in found.sections] == [("keys", "ERROR"), ("tokens", "ERROR")]
    assert "ENS_KEYSTORE_TOKEN" not in strings(found)


def test_keystore_sections_without_missing_reason_use_worst_status(store):
    sections = {
        "keys": {"status": "NOT_SUPPORTED", "reason": HOSTILE},
        "tokens": {"status": "PERMISSION_DENIED", "reason": HOSTILE},
    }
    imported(store, result("keystore", data={"sections": sections}))
    found = last(store, "keystore")
    assert found.failure.category == "permission_denied"
    assert HOSTILE not in strings(found)


def test_error_statuses_without_sections_nor_error_use_connector_error(store):
    imported(store, result("keystore", data={}))
    assert last(store, "keystore").failure.category == "connector_error"


def test_github_success_with_denied_section_is_partial_and_permission_denied(store):
    sections = {
        "organization": {"status": "SUCCESS"},
        "secret_scanning": {"status": "PERMISSION_DENIED", "reason": HOSTILE},
    }
    imported(store, result("github", "SUCCESS", {"sections": sections}))
    found = last(store, "github")
    assert found.status == "SUCCESS"
    assert found.partial
    assert found.failure.category == "permission_denied"
    assert [(s.name, s.status) for s in found.sections] == [
        ("organization", "SUCCESS"),
        ("secret_scanning", "PERMISSION_DENIED"),
    ]


def test_complete_success_has_no_failure_and_is_not_partial(store):
    imported(store, result("cloudflare", "SUCCESS", {"sections": {"zones": {"status": "SUCCESS"}}}))
    found = last(store, "cloudflare")
    assert found.failure is None
    assert not found.partial
    assert found.collected_at == "2026-11-17 12:00 UTC"


def test_cloudflare_partial_with_not_supported_section(store):
    sections = {"zones": {"status": "SUCCESS"}, "waf": {"status": "NOT_SUPPORTED"}}
    imported(store, result("cloudflare", "SUCCESS", {"sections": sections}))
    found = last(store, "cloudflare")
    assert found.partial
    assert found.failure.category == "not_supported"


def test_gws_key_rejected(store):
    imported(
        store, result("google-workspace", data={"error": "la clave de la cuenta de servicio x"})
    )
    assert last(store, "google-workspace").failure.category == "credential_rejected"


@pytest.mark.parametrize(
    ("code", "label"),
    [(c, rec.PASSWORD_LABELS[c]) for c in rec.PASSWORD_LABELS],
)
def test_passwords_reason_comes_only_from_the_code(store, code, label):
    imported(store, result("passwords", data={"error": code}))
    found = last(store, "passwords")
    assert found.failure.category == "connector_error"
    assert found.failure.detail == label
    assert code not in strings(found)


def test_passwords_unknown_code_uses_fixed_fallback(store):
    imported(store, result("passwords", data={"error": HOSTILE}))
    found = last(store, "passwords")
    assert found.failure.detail == rec.NO_EXPORT_DETAIL
    assert HOSTILE not in strings(found)


def test_restic_password_wrong(store):
    imported(
        store, result("restic", data={"error": "contraseña del repositorio incorrecta (código 12)"})
    )
    assert last(store, "restic").failure.category == "credential_rejected"


@pytest.mark.parametrize(
    ("phase", "error", "detail"),
    [
        ("prepare", "prepare_crashed:WorkspaceError", rec.CRASHED_DETAIL),
        ("result_rejected", "secret_detected:ghp_x", rec.SECRET_DETAIL),
        ("result_rejected", "identity_mismatch:asset_id", rec.IDENTITY_DETAIL),
        ("timeout", "timeout", rec.PHASE_LABELS["timeout"]),
        ("config", "algo " + HOSTILE, rec.PHASE_LABELS["config"]),
        ("fase_inventada", "algo", rec.GENERIC_SCHEDULER_DETAIL),
    ],
)
def test_scheduler_synthetic_codes_take_precedence(store, phase, error, detail):
    data = {"error": error, "generated_by": "scheduler", "phase": phase}
    imported(store, result("github", data=data))
    found = last(store, "github")
    assert found.failure.category == "connector_error"
    assert found.failure.detail == detail
    text = strings(found)
    assert "ghp_x" not in text
    assert "WorkspaceError" not in text
    assert "ENS_" not in text


def test_scheduler_code_wins_over_known_literal(store):
    data = {
        "error": "ENS_GITHUB_TOKEN no está definida",
        "generated_by": "scheduler",
        "phase": "collect",
    }
    imported(store, result("github", data=data))
    assert last(store, "github").failure.category == "connector_error"


def test_error_literal_wins_over_section_statuses(store):
    data = {
        "error": "HTTP 401: credencial rechazada",
        "sections": {"a": {"status": "NOT_SUPPORTED"}},
    }
    imported(store, result("github", data=data))
    assert last(store, "github").failure.category == "credential_rejected"


def test_categorize_returns_none_for_a_complete_success():
    assert rec.categorize("github", result("github", "SUCCESS", {})) is None


# --- validación del artefacto (D8) ---------------------------------------------------------------


def test_malformed_but_correctly_hashed_artifact_omits_sections_and_flags_it(store):
    sections = {
        "ghp_abcdefghijklmnopqrstuvwxyz0123456789": {"status": "SUCCESS"},
        "Mayusculas": {"status": "SUCCESS"},
        "ok_name": {"status": "SUCCESS"},
        "raro": {"status": "DESCONOCIDO " + HOSTILE},
        "sin_estado": {"reason": HOSTILE},
        "no_dict": "x",
    }
    manifest = put_raw(store, document(data={"sections": sections}))
    found = rec.last_collection(store, manifest, "github")
    assert [s.name for s in found.sections] == ["ok_name"]
    assert found.sections_unreadable
    assert not found.partial
    assert "ghp_" not in strings(found)
    assert HOSTILE not in strings(found)


def test_sections_not_a_mapping_are_flagged(store):
    manifest = put_raw(store, document(data={"sections": ["a", "b"]}))
    found = rec.last_collection(store, manifest, "github")
    assert found.sections == ()
    assert found.sections_unreadable


def test_wrong_sha_is_unreadable(store):
    good = put_raw(store, document())
    bad = good.model_copy(
        update={"artifact": good.artifact.model_copy(update={"sha256": "0" * 64})}
    )
    found = rec.last_collection(store, bad, "github")
    assert found.unreadable
    assert found.status is None
    assert found.status_label == rec.UNREADABLE_LABEL
    assert found.collected_at == "2026-11-17 12:00 UTC"


def test_tampered_content_with_same_key_is_unreadable(store):
    manifest = put_raw(store, document())
    store._inner.write_bytes(manifest.artifact.key, document(status="ERROR"))
    assert rec.last_collection(store, manifest, "github").unreadable


def test_oversize_artifact_is_unreadable(store, monkeypatch):
    monkeypatch.setattr(rec, "MAX_ARTIFACT_BYTES", 64)
    manifest = put_raw(store, document(data={"pad": "x" * 200}))
    assert rec.last_collection(store, manifest, "github").unreadable


def test_foreign_key_is_never_read(store):
    store._inner.write_text("settings/ui-roles.yaml", "secreto: " + HOSTILE)
    sha = hashlib.sha256(b"secreto: " + HOSTILE.encode()).hexdigest()
    manifest = put_raw(store, document(), key="settings/ui-roles.yaml")
    manifest = manifest.model_copy(
        update={"artifact": manifest.artifact.model_copy(update={"sha256": sha})}
    )
    reads = []
    original = store.read_bytes
    store.read_bytes = lambda key, limit=None: (reads.append(key), original(key, limit))[1]
    found = rec.last_collection(store, manifest, "github")
    assert found.unreadable
    assert reads == []


def test_non_workspace_storage_is_unreadable(store):
    manifest = put_raw(store, document())
    other = manifest.model_copy(
        update={"artifact": manifest.artifact.model_copy(update={"storage": "s3"})}
    )
    assert rec.last_collection(store, other, "github").unreadable


def test_not_json_or_not_a_collection_is_unreadable(store):
    assert rec.last_collection(store, put_raw(store, b"no es json"), "github").unreadable
    assert rec.last_collection(store, put_raw(store, b'{"source": "github"}'), "github").unreadable
    assert rec.last_collection(store, put_raw(store, b"[" * 100000), "github").unreadable


def test_identity_mismatch_is_unreadable(store):
    foreign = put_raw(store, document(name="cloudflare"), name="github")
    assert rec.last_collection(store, foreign, "github").unreadable
    other_asset = put_raw(store, document(asset="otro"), asset="asset-1")
    assert rec.last_collection(store, other_asset, "github").unreadable


# --- índice (D12) --------------------------------------------------------------------------------


def test_index_takes_latest_per_collector_and_ignores_manual(store):
    old = imported(store, result("github", at=NOW - timedelta(days=1)))
    new = imported(store, result("github", at=NOW))
    imported(store, result("cloudflare", at=NOW - timedelta(hours=1)))
    put_raw(store, document(data={"x": 1}), name="github", kind="manual")
    index = rec.index_collections(store)
    assert set(index.latest) == {"github", "cloudflare"}
    assert index.latest["github"].id == new.id != old.id
    assert index.unreadable_manifests == 0


def test_index_ignores_manifest_whose_collector_differs_from_source(store):
    sha = hashlib.sha256(b"x").hexdigest()
    manifest = EvidenceManifest(
        id="EV-20261117-aaaaaaaa",
        source="github",
        asset_id="a",
        collected_at=NOW,
        artifact=Artifact(storage="workspace", key=artifact_key(sha), sha256=sha),
        provenance=Provenance(kind=ProvenanceKind.COLLECTOR, submitted_by="c", submitted_at=NOW),
        collector=CollectorInfo(name="cloudflare", version="1"),
    )
    store._inner.write_text(manifest_key(manifest.id), render_manifest(manifest).decode())
    assert rec.index_collections(store).latest == {}


def test_index_counts_unreadable_manifests(store):
    store._inner.write_text("evidence/manifests/EV-20261117-bbbbbbbb.yaml", "no: [valido")
    assert rec.index_collections(store).unreadable_manifests == 1


def test_index_calls_list_manifests_once(store, monkeypatch):
    calls = []
    original = rec.list_manifests
    monkeypatch.setattr(rec, "list_manifests", lambda s: (calls.append(1), original(s))[1])
    imported(store, result("github"))
    rec.index_collections(store)
    assert len(calls) == 1


# --- backups por repositorio (D3) ----------------------------------------------------------------


def backups_data():
    return {
        "repositories": [
            {"id": "caja", "collection_status": "SUCCESS", "error": None},
            {
                "id": "nube",
                "collection_status": "ERROR",
                "error": "contraseña del repositorio incorrecta (código 12)",
            },
            {"id": "ajeno", "collection_status": "ERROR", "error": HOSTILE},
        ]
    }


def test_backups_two_repositories_in_different_states_and_foreign_id_ignored(store):
    res = result("backups", "ERROR", backups_data(), asset="backups")
    manifest = imported(store, res)
    states = rec.repository_states(store, manifest, ["caja", "nube"])
    assert [(s.id, s.status) for s in states] == [("caja", "SUCCESS"), ("nube", "ERROR")]
    assert states[0].failure is None
    assert states[1].failure.category == "credential_rejected"
    text = " | ".join(strings(s) for s in states)
    assert "ajeno" not in text
    assert HOSTILE not in text
    assert "incorrecta" not in text


def test_backups_configured_repository_missing_from_collection_has_no_status(store):
    manifest = imported(store, result("backups", "SUCCESS", backups_data(), asset="backups"))
    (state,) = rec.repository_states(store, manifest, ["otro"])
    assert state.status is None
    assert state.status_label == rec.UNREADABLE_LABEL


def test_backups_repository_with_unknown_status_has_no_status(store):
    data = {"repositories": [{"id": "caja", "collection_status": "MAGIA", "error": None}]}
    manifest = imported(store, result("backups", "SUCCESS", data, asset="backups"))
    (state,) = rec.repository_states(store, manifest, ["caja"])
    assert state.status is None


def test_backups_malformed_repositories_are_ignored(store):
    data = {"repositories": ["x", {"id": 3}, {"id": "caja", "collection_status": "ERROR"}]}
    manifest = imported(store, result("backups", "ERROR", data, asset="backups"))
    (state,) = rec.repository_states(store, manifest, ["caja"])
    assert state.failure.category == "connector_error"


def test_backups_unreadable_artifact_gives_no_repository_status(store):
    manifest = put_raw(
        store, document(name="backups", asset="backups"), name="backups", asset="backups"
    )
    bad = manifest.model_copy(
        update={"artifact": manifest.artifact.model_copy(update={"sha256": "1" * 64})}
    )
    (state,) = rec.repository_states(store, bad, ["caja"])
    assert state.status is None


# --- sin texto en bruto --------------------------------------------------------------------------


def test_asdict_of_every_dto_has_no_raw_error_text(store):
    errors = [
        "ENS_GITHUB_TOKEN no está definida",
        "HTTP 401: credencial rechazada",
        "HTTP 403: permiso insuficiente",
        HOSTILE,
    ]
    for error in errors:
        imported(store, result("github", data={"error": error}))
        found = last(store, "github")
        flat = json.dumps(asdict(found), ensure_ascii=False)
        assert error not in flat
        assert "token" not in flat.lower().replace("credencial", "")
