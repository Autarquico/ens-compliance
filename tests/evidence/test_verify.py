import pytest

from ens_evidence.evidence import (
    add_review,
    import_manual,
    verify_all,
    verify_one,
)
from ens_evidence.evidence.layout import manifest_key, sha256_hex
from ens_evidence.models import Review, Role

from .helpers import NOW

DATA = b"contenido original"


@pytest.fixture
def manifest(store):
    return import_manual(store, DATA, asset_id="a", submitted_by="ana", now=NOW)


def test_evidencia_intacta_verifica(store, manifest):
    assert verify_one(store, manifest.id).ok
    assert verify_all(store).ok


def test_evidencia_alterada_falla_mostrando_hash_esperado_y_obtenido(store, manifest):
    altered = bytearray(DATA)
    altered[0] ^= 0x01
    store.write_bytes(manifest.artifact.key, bytes(altered))

    for report in (verify_one(store, manifest.id), verify_all(store)):
        assert not report.ok
        assert f"hash esperado {sha256_hex(DATA)}" in report.problems[0]
        assert f"obtenido {sha256_hex(bytes(altered))}" in report.problems[0]


def test_verify_all_informa_artefactos_huerfanos(store, manifest):
    orphan = "evidence/raw/" + sha256_hex(b"huerfano")
    store.create_bytes(orphan, b"huerfano")

    report = verify_all(store)

    assert not report.ok
    assert [p for p in report.problems if "huérfano" in p] == [
        f"{orphan}: artefacto huérfano, ningún manifiesto lo referencia"
    ]


def test_verify_all_informa_manifiestos_que_apuntan_a_artefactos_inexistentes(store, manifest):
    other = import_manual(store, b"otro", asset_id="a", submitted_by="ana", now=NOW)
    store.write_bytes(other.artifact.key, other.artifact.key.encode())  # sustituido, no borrado
    missing = import_manual(store, b"tercero", asset_id="a", submitted_by="ana", now=NOW)
    _delete(store, missing.artifact.key)

    report = verify_all(store)

    assert any(
        p == f"{missing.id}: falta el artefacto {missing.artifact.key}" for p in report.problems
    )
    assert not any("huérfano" in p for p in report.problems)


def _delete(store, key):
    if hasattr(store, "_root"):
        (store._root / key).unlink()
    else:
        store._client.delete_object(Bucket=store._bucket, Key=store._full(key))


def test_verify_one_de_una_evidencia_inexistente_falla(store):
    report = verify_one(store, "EV-20260601-deadbeef")

    assert not report.ok
    assert "no existe" in report.problems[0]


def test_verify_one_con_id_mal_formado_falla_sin_tocar_el_almacenamiento(store):
    report = verify_one(store, "../../scope/system")

    assert not report.ok
    assert "no es un identificador de evidencia" in report.problems[0]


def test_manifiesto_ilegible_se_informa_y_deja_su_artefacto_como_huerfano(store, manifest):
    store.write_text(manifest_key(manifest.id), "no: [valido")

    report = verify_all(store)

    assert not report.ok
    assert any("YAML no válido" in p for p in report.problems)
    assert any("huérfano" in p for p in report.problems)


def review(**overrides):
    base = {
        "reviewer": "ana",
        "role": Role.SECURITY_OFFICER,
        "reviewed_at": NOW,
        "valid_until": NOW.replace(year=2027),
        "conclusion": "ok",
    }
    return Review(**{**base, **overrides})


def test_las_revisiones_se_anaden_sin_modificar_el_manifiesto(store, manifest):
    before = store.read_bytes(manifest_key(manifest.id))

    first = add_review(store, manifest.id, review())
    second = add_review(store, manifest.id, review(reviewed_at=NOW.replace(day=2), reviewer="luis"))

    assert store.read_bytes(manifest_key(manifest.id)) == before
    assert first != second
    assert sorted(store.list(f"evidence/reviews/{manifest.id}")) == sorted([first, second])
    assert verify_all(store).ok


def test_una_revision_no_sobrescribe_otra_del_mismo_instante(store, manifest):
    from ens_evidence.workspace import AlreadyExists

    add_review(store, manifest.id, review())

    with pytest.raises(AlreadyExists):
        add_review(store, manifest.id, review(conclusion="distinta"))


def test_no_se_puede_revisar_una_evidencia_inexistente(store):
    from ens_evidence.workspace import WorkspaceError

    with pytest.raises(WorkspaceError, match="no existe"):
        add_review(store, "EV-20260601-deadbeef", review())
