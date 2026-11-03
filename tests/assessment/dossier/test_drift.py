"""Deriva: qué cambios del workspace invalidan el expediente y cuáles no."""

from datetime import timedelta

import pytest
from checks.acceptance_helpers import CHECK_MEASURE, COLLECTED
from matrix.evaluate_helpers import NOW

from ens_evidence.assessment.dossier import build_snapshot, diff_projection, snapshot_sha256
from ens_evidence.assessment.dossier.drift import REBUILD, rebuild_and_diff
from ens_evidence.assessment.dossier.model import DossierSnapshot
from ens_evidence.assessment.dossier.store import load_snapshot, stored_snapshot_sha256
from ens_evidence.assessment.dossier.transitions import TransitionRejected
from ens_evidence.checks.source import StoredCheckResults
from ens_evidence.matrix import assess
from ens_evidence.models.enums import Readiness

from .transition_helpers import RECORDED, generate, self_assess
from .worlds import INPUTS, edit_answers


def test_sin_cambios_no_hay_deriva_aunque_pase_el_tiempo(basic):
    snapshot = build_snapshot(basic, INPUTS)

    report = rebuild_and_diff(basic, snapshot, checks=StoredCheckResults(reference_now=RECORDED))

    assert report.current
    assert report.drift == {}


def test_editar_las_respuestas_organizativas_deriva(docs):
    snapshot = build_snapshot(docs, INPUTS)

    edit_answers(docs, "mision", "Otra misión distinta de la registrada.")
    report = rebuild_and_diff(docs, snapshot)

    assert not report.current
    assert any("org/answers.yaml" in line for lines in report.drift.values() for line in lines)


def test_unas_entradas_de_otra_version_no_se_reproducen_y_se_informan(basic):
    snapshot = build_snapshot(basic, INPUTS)
    other = snapshot.model_copy(
        update={"inputs": snapshot.inputs.model_copy(update={"product_version": "0.0.0"})}
    )

    report = rebuild_and_diff(basic, other)

    assert list(report.drift) == [REBUILD]
    assert "inputs.product_version" in report.drift[REBUILD][0]


def test_editar_la_matriz_deriva_y_bloquea_las_transiciones(ready):
    dossier = generate(ready.store)
    snapshot = load_snapshot(ready.store, dossier)

    ready.at(NOW - timedelta(minutes=10))
    assert ready.run("matrix", "set", CHECK_MEASURE, "--every", "9").exit_code == 0

    report = rebuild_and_diff(ready.store, snapshot)
    assert list(report.drift) == ["7. Resultado por medida"]
    assert any("controls/matrix.yaml" in line for line in report.drift["7. Resultado por medida"])
    with pytest.raises(TransitionRejected) as error:
        self_assess(ready.store, dossier)
    assert "ya no describe el estado actual" in "\n".join(error.value.errors)
    assert "controls/matrix.yaml" in "\n".join(error.value.errors)
    assert not [k for k in ready.store.list("evidence/assessments") if "transitions" in k]


def test_una_ejecucion_nueva_y_equivalente_no_deriva_y_se_puede_seguir(ready):
    dossier = generate(ready.store)
    snapshot = load_snapshot(ready.store, dossier)

    ready.collect(at=COLLECTED + timedelta(hours=3))
    ready.check_all(NOW - timedelta(minutes=45))

    assert rebuild_and_diff(ready.store, snapshot).current
    self_assess(ready.store, dossier)


def test_una_ejecucion_fallida_no_deriva_pero_la_evaluacion_actual_no_esta_ready(ready):
    dossier = generate(ready.store)
    snapshot = load_snapshot(ready.store, dossier)

    ready.collect(secret_scanning_off=True, at=COLLECTED + timedelta(hours=3))
    ready.check_all(NOW - timedelta(minutes=45))

    assert rebuild_and_diff(ready.store, snapshot).current
    current = assess(ready.store, RECORDED, checks=StoredCheckResults(reference_now=RECORDED))
    assert snapshot.sections[6].content["matrix"]["readiness"] == Readiness.READY
    assert current.readiness.readiness is Readiness.NOT_READY


def test_un_check_all_posterior_a_la_generacion_no_rompe_la_reproduccion(ready):
    dossier = generate(ready.store)
    snapshot = load_snapshot(ready.store, dossier)

    ready.collect(at=NOW + timedelta(minutes=10))
    ready.check_all(NOW + timedelta(minutes=30))

    assert rebuild_and_diff(ready.store, snapshot).current
    rebuilt = build_snapshot(ready.store, snapshot.inputs)
    assert snapshot_sha256(rebuilt) == stored_snapshot_sha256(ready.store, dossier)
    verified = ready.run("assessment", "dossier", "verify", dossier)
    assert verified.exit_code == 0, verified.output


def test_una_ejecucion_posterior_no_cambia_las_fuentes_de_la_instantanea(ready):
    first = build_snapshot(ready.store, INPUTS)

    ready.collect(at=NOW + timedelta(minutes=10))
    ready.check_all(NOW + timedelta(minutes=30))

    again = build_snapshot(ready.store, first.inputs)
    assert snapshot_sha256(again) == snapshot_sha256(first)


def _with_incident(snapshot, status: str):
    document = snapshot.model_dump(mode="json")
    section = document["sections"][7]
    section["content"]["incidents"].append(
        {
            "id": "INC-2026-001",
            "simulated": False,
            "status": status,
            "detected_at": "2026-10-30T10:00:00+00:00",
            "records": 1,
            "problems": [],
        }
    )
    section["sources"].append(
        {
            "kind": "incident-record",
            "key": "incidents/INC-2026-001/000001-x.yaml",
            "sha256": "a" * 64,
            "timestamps": ["2026-10-30T10:00:00+00:00"],
        }
    )
    return DossierSnapshot.model_validate(document)


def test_un_incidente_abierto_no_deriva_y_uno_cerrado_si(basic):
    snapshot = build_snapshot(basic, INPUTS)

    assert diff_projection(snapshot, _with_incident(snapshot, "open")) == {}
    closed = diff_projection(snapshot, _with_incident(snapshot, "closed"))
    assert list(closed) == ["8. Evidencias"]
