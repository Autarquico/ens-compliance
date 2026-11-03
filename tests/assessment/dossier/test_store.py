"""Almacenamiento verificable de la instantánea: id, solo adición y lectura estricta."""

import json
from datetime import timedelta

import pytest

from ens_evidence.assessment.dossier import build_snapshot
from ens_evidence.assessment.dossier.store import (
    DossierUnreadable,
    dossier_id,
    list_dossiers,
    load_snapshot,
    save_markdown,
    save_snapshot,
    stored_snapshot_sha256,
)
from ens_evidence.workspace import AlreadyExists

from .worlds import INPUTS, PASSWORD

KEY = "evidence/assessments/{}/dossier.json"


@pytest.fixture
def saved(basic):
    snapshot = build_snapshot(basic, INPUTS)
    return basic, snapshot, save_snapshot(basic, snapshot)


def _plant(store, data: bytes, generated_at) -> str:
    """Guarda `data` bajo un id coherente con sus bytes, como haría quien lo edita a mano."""
    dossier = dossier_id(data, generated_at)
    store.create_bytes(KEY.format(dossier), data)
    return dossier


def test_guardar_y_leer(saved):
    store, snapshot, dossier = saved

    assert load_snapshot(store, dossier) == snapshot
    assert dossier.endswith(stored_snapshot_sha256(store, dossier)[:12])


def test_el_instante_retocado_se_rechaza(saved):
    store, snapshot, dossier = saved
    data = store.read_bytes(KEY.format(dossier))
    other = dossier_id(data, snapshot.inputs.generated_at + timedelta(seconds=1))
    store.create_bytes(KEY.format(other), data)

    with pytest.raises(DossierUnreadable, match="instante"):
        load_snapshot(store, other)


def test_un_byte_cambiado_se_rechaza(saved):
    store, _, dossier = saved
    data = store.read_bytes(KEY.format(dossier))
    store.write_bytes(KEY.format(dossier), data.replace(b"Identificaci", b"Identificacj", 1))

    with pytest.raises(DossierUnreadable, match="huella"):
        load_snapshot(store, dossier)


def test_un_id_que_no_coincide_se_rechaza(saved):
    store, _, dossier = saved
    data = store.read_bytes(KEY.format(dossier))
    wrong = dossier[:-12] + "0" * 12
    store.create_bytes(KEY.format(wrong), data)

    with pytest.raises(DossierUnreadable, match="huella"):
        load_snapshot(store, wrong)
    with pytest.raises(DossierUnreadable, match="forma"):
        load_snapshot(store, "../otro")


def test_las_claves_duplicadas_se_rechazan(saved):
    store, snapshot, dossier = saved
    text = store.read_bytes(KEY.format(dossier)).decode()
    doubled = text.replace('"commitment"', '"category": "BASICA",\n  "commitment"', 1)
    assert doubled != text
    forged = _plant(store, doubled.encode(), snapshot.inputs.generated_at)

    with pytest.raises(DossierUnreadable, match="estricta"):
        load_snapshot(store, forged)


def test_un_secreto_introducido_a_mano_se_rechaza_sin_el_valor(saved):
    store, snapshot, dossier = saved
    document = json.loads(store.read_bytes(KEY.format(dossier)))
    document["sections"][9]["content"] = {"conclusions": f"password={PASSWORD}"}
    forged = _plant(
        store, (json.dumps(document, indent=2) + "\n").encode(), snapshot.inputs.generated_at
    )

    with pytest.raises(DossierUnreadable) as error:
        load_snapshot(store, forged)
    assert error.value.problems
    assert PASSWORD not in str(error.value)


def test_solo_adicion(saved):
    store, snapshot, dossier = saved

    with pytest.raises(AlreadyExists):
        save_snapshot(store, snapshot)
    save_markdown(store, dossier, "# uno\n")
    with pytest.raises(AlreadyExists):
        save_markdown(store, dossier, "# dos\n")
    assert store.read_text(f"evidence/assessments/{dossier}/dossier.md") == "# uno\n"


def test_listar_incluye_los_ilegibles(saved):
    store, snapshot, dossier = saved
    bad = dossier_id(b"{}", snapshot.inputs.generated_at + timedelta(days=1))
    store.create_bytes(KEY.format(bad), b"{}")

    listed = {d.id: d for d in list_dossiers(store)}

    assert listed[dossier].snapshot == snapshot and not listed[dossier].problems
    assert listed[bad].snapshot is None and listed[bad].problems
