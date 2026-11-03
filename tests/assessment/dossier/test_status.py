"""Estado del expediente: estado reconstruido, deriva, integridad, huellas y paso siguiente."""

import pytest

from ens_evidence.assessment.dossier.model import INTERNAL_REVIEW_NOTICE
from ens_evidence.assessment.dossier.status import (
    RECOVER,
    RECOVER_SNAPSHOT,
    REGENERATE,
    dossier_status,
)
from ens_evidence.assessment.dossier.store import ASSESSMENTS_PREFIX
from ens_evidence.assessment.dossier.transitions import (
    CERTIFICATION_NOTE,
    TransitionRejected,
    entries,
    seq_key,
)
from ens_evidence.models.enums import Category, DossierState

from .stages import STEP_AT, basic, restore
from .transition_helpers import (
    analyze,
    approve_designation,
    generate,
    self_assess,
)
from .worlds import edit_answers


def test_un_expediente_recien_generado_espera_la_autoevaluacion(docs):
    dossier = generate(docs)

    status = dossier_status(docs, dossier)

    assert status.state is DossierState.GENERATED
    assert status.category is Category.BASICA
    assert status.notice is None
    assert status.integrity == ()
    assert status.drift is not None and status.drift.current
    assert "autoevaluación" in status.next_step
    assert status.fingerprints.snapshot
    assert status.fingerprints.analysis is None


def stage(name, tmp_path, monkeypatch):
    stages = basic()
    return restore(stages, name, tmp_path, monkeypatch).store, stages.dossier


def test_el_paso_siguiente_avanza_con_cada_transicion(tmp_path, monkeypatch):
    store, dossier = stage("self-assess", tmp_path / "a", monkeypatch)
    assessed = dossier_status(store, dossier, now=STEP_AT["self-assess"])
    store, dossier = stage("analyze", tmp_path / "b", monkeypatch)
    analyzed = dossier_status(store, dossier, now=STEP_AT["analyze"])
    analysis = entries(store, dossier)[-1]
    store, dossier = stage("declare", tmp_path / "c", monkeypatch)
    done = dossier_status(store, dossier, now=STEP_AT["declare"])
    declaration = entries(store, dossier)[-1]

    assert "responsable de la seguridad" in assessed.next_step
    assert analyzed.state is DossierState.ANALYZED
    assert "dirección" in analyzed.next_step
    assert analyzed.fingerprints.analysis == analysis.sha256
    assert done.state is DossierState.DECLARED
    assert done.next_step.startswith("ninguno")
    assert done.fingerprints.cited_analysis == done.fingerprints.analysis == analysis.sha256
    assert done.fingerprints.last_transition == declaration.sha256


def test_una_cadena_rota_no_da_estado_y_manda_recuperar(tmp_path, monkeypatch):
    store, dossier = stage("analyze", tmp_path, monkeypatch)
    store._root.joinpath(seq_key(dossier, 2)).unlink()

    status = dossier_status(store, dossier, now=STEP_AT["analyze"])

    assert status.state is None
    assert status.integrity
    assert status.next_step == RECOVER


def test_un_expediente_ilegible_se_informa_sin_estado(docs):
    status = dossier_status(docs, "20260101T000000000000Z-aaaaaaaaaaaa")

    assert status.state is None and status.category is None and status.drift is None
    assert status.integrity and status.next_step == RECOVER_SNAPSHOT
    assert not status.known


def test_la_deriva_manda_generar_uno_nuevo(docs):
    dossier = generate(docs)
    edit_answers(docs, "mision", "Otra misión distinta de la registrada.")

    status = dossier_status(docs, dossier)

    assert status.drift is not None and not status.drift.current
    assert status.next_step == REGENERATE


def test_en_media_nunca_se_propone_declarar(ready):
    approve_designation(ready.store)
    dossier = generate(ready.store)
    first = dossier_status(ready.store, dossier)
    self_assess(ready.store, dossier)
    analyze(ready.store, dossier)

    last = dossier_status(ready.store, dossier)

    assert first.category is Category.MEDIA
    assert first.notice == INTERNAL_REVIEW_NOTICE
    assert INTERNAL_REVIEW_NOTICE not in first.next_step
    assert last.state is DossierState.ANALYZED
    assert last.next_step == f"ninguno: {CERTIFICATION_NOTE}"
    assert "declaración de conformidad" not in last.next_step


def test_en_media_con_el_paquete_editado_ninguna_transicion_se_registra(ready):
    approve_designation(ready.store)
    dossier = generate(ready.store)
    file = next(
        k for k in ready.store.list(f"{ASSESSMENTS_PREFIX}/{dossier}/package") if "index" not in k
    )
    (ready.store._root / file).write_text("falso\n")

    status = dossier_status(ready.store, dossier)
    with pytest.raises(TransitionRejected) as error:
        self_assess(ready.store, dossier)

    assert any(file in problem for problem in status.integrity)
    assert file in "\n".join(error.value.errors)
    assert dossier_status(ready.store, dossier).state is DossierState.GENERATED
