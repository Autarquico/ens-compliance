"""Estado del expediente: estado reconstruido, deriva, integridad, huellas y paso siguiente."""

from ens_evidence.assessment.dossier.model import INTERNAL_REVIEW_NOTICE
from ens_evidence.assessment.dossier.status import (
    RECOVER,
    RECOVER_SNAPSHOT,
    REGENERATE,
    dossier_status,
)
from ens_evidence.assessment.dossier.transitions import CERTIFICATION_NOTE, seq_key
from ens_evidence.models.enums import Category, DossierState

from .transition_helpers import (  # noqa: F401
    RECORDED,
    analyze,
    approve_designation,
    declare,
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


def test_el_paso_siguiente_avanza_con_cada_transicion(docs, stand_ins):
    dossier = generate(docs)

    self_assess(docs, dossier)
    assert "responsable de la seguridad" in dossier_status(docs, dossier).next_step
    analysis = analyze(docs, dossier)
    after_analysis = dossier_status(docs, dossier, now=RECORDED)
    declaration = declare(docs, dossier)
    done = dossier_status(docs, dossier)

    assert after_analysis.state is DossierState.ANALYZED
    assert "no se puede declarar" in after_analysis.next_step  # el mundo `docs` no está READY
    assert after_analysis.fingerprints.analysis == analysis.entry.sha256
    assert done.state is DossierState.DECLARED
    assert done.next_step.startswith("ninguno")
    assert done.fingerprints.cited_analysis == done.fingerprints.analysis == analysis.entry.sha256
    assert done.fingerprints.last_transition == declaration.entry.sha256


def test_una_cadena_rota_no_da_estado_y_manda_recuperar(docs, stand_ins):
    dossier = generate(docs)
    self_assess(docs, dossier)
    analyze(docs, dossier)
    docs._root.joinpath(seq_key(dossier, 2)).unlink()

    status = dossier_status(docs, dossier)

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
    assert INTERNAL_REVIEW_NOTICE in first.next_step
    assert last.state is DossierState.ANALYZED
    assert last.next_step == f"ninguno: {CERTIFICATION_NOTE}"
    assert "declaración de conformidad" not in last.next_step
