"""Paquete por medida: una entrada por fila, manifiesto cerrado y verificación de lo citado."""

import json
from datetime import timedelta

import pytest
import yaml
from matrix.evaluate_helpers import NOW

from ens_evidence.assessment.dossier import build_snapshot
from ens_evidence.assessment.dossier.package import (
    INDEX_NAME,
    PackageRejected,
    build_package,
    save_package,
    verify_package,
)
from ens_evidence.assessment.dossier.store import save_snapshot
from ens_evidence.evidence.importer import import_manual
from ens_evidence.matrix import MATRIX_KEY
from ens_evidence.workspace import AlreadyExists

from .worlds import INPUTS, PASSWORD

PREFIX = "evidence/assessments/{}/package"


@pytest.fixture
def saved(ready):
    snapshot = build_snapshot(ready.store, INPUTS)
    return ready.store, snapshot, save_snapshot(ready.store, snapshot)


def _index(package) -> dict:
    return json.loads(package.files[INDEX_NAME])


def _measures(package) -> dict[str, dict]:
    return {n: json.loads(d) for n, d in package.files.items() if n != INDEX_NAME}


def _artifact_key(package) -> str:
    return _index(package)["artifacts"][0]["key"]


def test_una_entrada_por_cada_fila_de_la_declaracion(ready):
    snapshot = build_snapshot(ready.store, INPUTS)
    declaration = snapshot.sections[4].content["declaration"]
    declared = {m["id"] for m in declaration["selected"] + declaration["not_selected"]}

    package = build_package(snapshot)

    rows = {m["measure_id"] for m in _measures(package).values()}
    assert rows == {n.removesuffix(".json") for n in _measures(package)}
    assert rows == declared
    assert {e["file"] for e in _index(package)["measures"]} == set(_measures(package))


def test_cada_medida_trae_obligaciones_enlaces_revision_evidencia_y_bloqueos(ready):
    package = build_package(build_snapshot(ready.store, INPUTS))
    measure = _measures(package)["mp.com.1.json"]

    assert measure["obligations"][0]["clauses"] == ["mp.com.1.1", "mp.com.1.2"]
    assert measure["links"][0]["covers"] == ["base"]
    assert measure["review"]["key"].startswith("controls/reviews/mp.com.1/")
    assert len(measure["review"]["file_sha256"]) == 64
    assert measure["evidence"][0]["manifest_sha256"] and measure["evidence"][0]["artifact_sha256"]
    assert measure["blockers"] == []


def test_los_bloqueos_de_cada_medida_se_recogen(basic):
    package = build_package(build_snapshot(basic, INPUTS))

    assert any(m["blockers"] for m in _measures(package).values())


def test_las_ejecuciones_de_checks_llevan_clave_y_huella(ready):
    package = build_package(build_snapshot(ready.store, INPUTS))

    runs = [
        r for m in _measures(package).values() for x in m["check_executions"] for r in x["runs"]
    ]

    assert runs
    assert all(r["key"].startswith("evidence/checks/") and len(r["sha256"]) == 64 for r in runs)


def test_media_lleva_la_etiqueta_de_revision_interna(ready):
    package = build_package(build_snapshot(ready.store, INPUTS))

    assert "art. 38.1" in _index(package)["internal_review"]
    assert all("art. 38.1" in m["internal_review"] for m in _measures(package).values())


def test_basica_no_lleva_etiqueta(basic):
    package = build_package(build_snapshot(basic, INPUTS))

    assert _index(package)["internal_review"] is None
    assert all(m["internal_review"] is None for m in _measures(package).values())


def test_el_paquete_es_determinista(ready):
    first = build_package(build_snapshot(ready.store, INPUTS))
    second = build_package(build_snapshot(ready.store, INPUTS))

    assert first.files == second.files


def test_guardar_y_verificar(saved):
    store, snapshot, dossier = saved

    save_package(store, dossier, snapshot)

    report = verify_package(store, dossier)
    assert report.problems == []
    assert report.checked > 1
    with pytest.raises(AlreadyExists):
        save_package(store, dossier, snapshot)


def test_una_instantanea_ajena_no_se_guarda(saved, ready):
    store, snapshot, dossier = saved
    other = snapshot.model_copy(
        update={"inputs": snapshot.inputs.model_copy(update={"reference_now": NOW + timedelta(1)})}
    )

    with pytest.raises(PackageRejected, match="no es la guardada"):
        save_package(store, dossier, other)


def test_un_artefacto_modificado_da_error(saved):
    store, snapshot, dossier = saved
    save_package(store, dossier, snapshot)
    key = _artifact_key(build_package(snapshot))
    store.write_bytes(key, b"otro contenido")

    problems = verify_package(store, dossier).problems

    assert any(key in p and "ha cambiado" in p for p in problems)


def test_un_artefacto_ausente_da_error(saved):
    store, snapshot, dossier = saved
    save_package(store, dossier, snapshot)
    key = _artifact_key(build_package(snapshot))
    (store._root / key).unlink()

    problems = verify_package(store, dossier).problems

    assert any(key in p and "no existe" in p for p in problems)


def test_un_manifiesto_modificado_o_ausente_da_error(saved):
    store, snapshot, dossier = saved
    save_package(store, dossier, snapshot)
    first, second = _index(build_package(snapshot))["manifests"][:2]
    store.write_bytes(first["key"], store.read_bytes(first["key"]) + b"# editado\n")
    (store._root / second["key"]).unlink()

    problems = verify_package(store, dossier).problems

    assert any(first["key"] in p and "ha cambiado" in p for p in problems)
    assert any(second["key"] in p and "no existe" in p for p in problems)


def test_un_fichero_del_paquete_modificado_ausente_o_ajeno_da_error(saved):
    store, snapshot, dossier = saved
    save_package(store, dossier, snapshot)
    prefix = PREFIX.format(dossier)
    store.write_bytes(f"{prefix}/mp.com.1.json", b"{}\n")
    (store._root / prefix / "mp.com.2.json").unlink()
    store.create_bytes(f"{prefix}/extra.json", b"{}\n")

    problems = verify_package(store, dossier).problems

    assert any("mp.com.1.json" in p and "ha cambiado" in p for p in problems)
    assert any("mp.com.2.json" in p and "no existe" in p for p in problems)
    assert any("extra.json" in p and "ajeno" in p for p in problems)


def test_un_indice_ausente_o_ilegible_da_error(saved):
    store, snapshot, dossier = saved
    assert any("no existe" in p for p in verify_package(store, dossier).problems)

    save_package(store, dossier, snapshot)
    store.write_bytes(f"{PREFIX.format(dossier)}/{INDEX_NAME}", b'{"extra": 1}')

    assert any("ilegible" in p for p in verify_package(store, dossier).problems)


def test_un_artefacto_integro_con_credenciales_no_aparece_en_el_paquete(ready):
    store = ready.store
    manifest = import_manual(
        store,
        f"password={PASSWORD}\n".encode(),
        asset_id="a",
        submitted_by="Ana",
        now=NOW - timedelta(hours=3),
    )
    matrix = yaml.safe_load(store.read_text(MATRIX_KEY))
    matrix["entries"][0]["links"].append(
        {"kind": "evidence", "evidence_id": manifest.id, "covers": ["base"]}
    )
    store.write_text(MATRIX_KEY, yaml.safe_dump(matrix))
    snapshot = build_snapshot(store, INPUTS)
    dossier = save_snapshot(store, snapshot)

    save_package(store, dossier, snapshot)

    cited = [
        m for m in _index(build_package(snapshot))["manifests"] if m["evidence_id"] == manifest.id
    ]
    assert cited
    assert all(
        PASSWORD.encode() not in store.read_bytes(key) for key in store.list(PREFIX.format(dossier))
    )
    assert verify_package(store, dossier).problems == []
